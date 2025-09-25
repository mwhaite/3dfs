"""Utilities for importing external 3D assets into managed storage."""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlparse

try:  # pragma: no cover - import guard exercised via tests
    import trimesh
except ImportError:  # pragma: no cover - dependency guaranteed in production
    trimesh = None  # type: ignore[assignment]

from .config import get_config
from .import_plugins import get_plugin_for
from .paths import resolve_storage_root

if TYPE_CHECKING:  # pragma: no cover - used for type checking only
    from .storage import AssetRecord, AssetService

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "AssetImportError",
    "UnsupportedAssetTypeError",
    "default_storage_root",
    "import_asset",
    "load_trimesh_mesh",
    "extract_step_metadata",
]

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".fbx", ".gltf", ".glb", ".obj", ".ply", ".step", ".stl", ".stp"}
)
"""Supported file extensions for imported assets."""


def default_storage_root() -> Path:
    """Return the directory used when *storage_root* is omitted."""

    return get_config().library_root


DEFAULT_STORAGE_ROOT: Final[Path] = default_storage_root()
"""Default directory where imported assets are stored.

The value reflects the configuration when this module is imported. Use
:func:`default_storage_root` to resolve the path lazily after updating the
application configuration.
"""


_WINDOWS_DRIVE_PATTERN = re.compile(r"^[a-zA-Z]:")
_SANITIZE_FILENAME_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")


class AssetImportError(RuntimeError):
    """Base exception raised when an asset cannot be imported."""


class UnsupportedAssetTypeError(AssetImportError):
    """Raised when attempting to import an unsupported asset format."""


def import_asset(
    path: Path | str,
    *,
    service: AssetService | None = None,
    storage_root: Path | str | None = None,
) -> AssetRecord:
    """Import the asset referenced by *path* into managed storage.

    Parameters
    ----------
    path:
        Either a filesystem path or a remote identifier understood by an
        import plugin.
    service:
        Optional :class:`~three_dfs.storage.AssetService` used to register the
        asset. A new service instance is created when omitted.
    storage_root:
        Directory where managed copies of imported assets are persisted. When
        omitted the importer uses :func:`default_storage_root`.
    """

    identifier_str = str(path)
    looks_remote = _looks_like_remote_identifier(identifier_str)
    attempted_local_resolution = isinstance(path, Path) or not looks_remote

    candidate: Path | None = None
    if isinstance(path, Path):
        candidate = path.expanduser()
    elif attempted_local_resolution:
        candidate = Path(identifier_str).expanduser()

    source: Path | None = None
    if candidate is not None and attempted_local_resolution:
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError):
            resolved = None
        else:
            if not resolved.is_file():
                raise AssetImportError(f"Asset {resolved!s} is not a file")
            source = resolved

    managed_root = resolve_storage_root(
        storage_root,
        default=default_storage_root,
    )
    imported_at = datetime.now(UTC).isoformat()

    if source is not None:
        return _import_local_asset(
            source,
            identifier_str,
            imported_at,
            managed_root,
            service=service,
        )

    return _import_remote_asset(
        identifier_str,
        imported_at,
        managed_root,
        service=service,
        attempted_local_resolution=attempted_local_resolution,
    )


def _import_local_asset(
    source: Path,
    identifier: str,
    imported_at: str,
    managed_root: Path,
    *,
    service: AssetService | None,
) -> AssetRecord:
    from .storage.metadata import build_asset_metadata

    extension = source.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedAssetTypeError(
            f"Unsupported asset format '{extension or 'unknown'}'"
        )

    managed_root.mkdir(parents=True, exist_ok=True)
    destination = _allocate_destination(managed_root, source.name)
    shutil.copy2(source, destination)

    size = destination.stat().st_size
    metadata = build_asset_metadata(
        source=identifier,
        source_type="local",
        original_path=source,
        managed_path=destination,
        size=size,
        timestamps={"imported_at": imported_at},
        extra={"extension": extension.lstrip(".").upper()},
    )
    metadata.update(_extract_format_metadata(destination, extension))

    return _persist_record(destination, metadata, label=source.stem, service=service)


def _import_remote_asset(
    identifier: str,
    imported_at: str,
    managed_root: Path,
    *,
    service: AssetService | None,
    attempted_local_resolution: bool,
) -> AssetRecord:
    from .storage.metadata import build_asset_metadata

    plugin = get_plugin_for(identifier)
    if plugin is None:
        if attempted_local_resolution:
            raise FileNotFoundError(f"Asset {identifier!s} does not exist")
        raise AssetImportError(f"No import plugin available for {identifier}")

    managed_root.mkdir(parents=True, exist_ok=True)
    destination = _allocate_destination(
        managed_root, _derive_destination_name(identifier)
    )

    try:
        plugin_metadata_raw = plugin.fetch(identifier, destination)
    except Exception as exc:  # pragma: no cover - defensive safety net
        destination.unlink(missing_ok=True)
        raise AssetImportError(
            f"Import plugin {plugin.__class__.__name__} failed to fetch {identifier}"
        ) from exc

    try:
        plugin_metadata = dict(plugin_metadata_raw or {})
    except TypeError as exc:
        destination.unlink(missing_ok=True)
        raise AssetImportError(
            f"Import plugin {plugin.__class__.__name__} returned invalid metadata"
        ) from exc

    filename_override = plugin_metadata.get("filename")
    if filename_override:
        sanitized = Path(str(filename_override)).name
        if sanitized and sanitized != destination.name:
            new_destination = _allocate_destination(managed_root, sanitized)
            destination.rename(new_destination)
            destination = new_destination

    final_path = _resolve_plugin_destination(destination, plugin_metadata)
    declared_extension = _normalise_extension(plugin_metadata.get("extension"))

    if not final_path.exists():
        raise AssetImportError(
            "Import plugin "
            f"{plugin.__class__.__name__} did not materialize an asset for {identifier}"
        )

    if not final_path.is_file():
        raise AssetImportError(
            "Import plugin "
            f"{plugin.__class__.__name__} produced a non-file destination {final_path}"
        )

    if declared_extension and final_path.suffix.lower() != declared_extension:
        target_name = final_path.with_suffix(declared_extension).name
        replacement = _allocate_destination(final_path.parent, target_name)
        if replacement != final_path:
            final_path = final_path.rename(replacement)

    if final_path != destination and destination.exists():
        destination.unlink(missing_ok=True)

    extension = final_path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        final_path.unlink(missing_ok=True)
        raise UnsupportedAssetTypeError(
            "Unsupported asset format " f"'{extension or 'unknown'}' from import plugin"
        )

    plugin_label_value = plugin_metadata.get("label")
    plugin_label = str(plugin_label_value) if plugin_label_value is not None else None
    plugin_identifier = f"{plugin.__class__.__module__}.{plugin.__class__.__qualname__}"

    size = final_path.stat().st_size
    base_metadata = build_asset_metadata(
        source=identifier,
        source_type="remote",
        original_path=identifier,
        managed_path=final_path,
        size=size,
        timestamps={"imported_at": imported_at},
        extra={
            "extension": extension.lstrip(".").upper(),
            "remote_source": identifier,
            "import_plugin": plugin_identifier,
        },
    )
    metadata = dict(base_metadata)

    reserved_keys = {
        "filename",
        "extension",
        "managed_path",
        "original_path",
        "imported_at",
        "size",
        "source",
    }
    for key in reserved_keys:
        plugin_metadata.pop(key, None)

    metadata.update(plugin_metadata)
    metadata["managed_path"] = base_metadata["managed_path"]
    metadata["extension"] = base_metadata["extension"]
    metadata["size"] = base_metadata["size"]
    metadata["imported_at"] = base_metadata["imported_at"]
    metadata.setdefault("remote_source", base_metadata["remote_source"])
    metadata.setdefault("original_path", base_metadata["original_path"])
    metadata.setdefault("source_type", base_metadata["source_type"])
    metadata.setdefault("source", base_metadata["source"])

    metadata.update(_extract_format_metadata(final_path, extension))

    label = plugin_label or final_path.stem
    return _persist_record(final_path, metadata, label=label, service=service)


def _persist_record(
    final_path: Path,
    metadata: dict[str, Any],
    *,
    label: str,
    service: AssetService | None,
) -> AssetRecord:
    asset_service = service or _default_asset_service()
    try:
        record = asset_service.create_asset(
            str(final_path),
            label=label,
            metadata=metadata,
        )
    except Exception:
        final_path.unlink(missing_ok=True)
        raise

    return record


def _looks_like_remote_identifier(value: str) -> bool:
    parsed = urlparse(value)
    if not parsed.scheme:
        return False

    if parsed.scheme == "file":
        return False

    if len(parsed.scheme) == 1 and _WINDOWS_DRIVE_PATTERN.match(value):
        return False

    return True


def _derive_destination_name(source: str) -> str:
    parsed = urlparse(source)
    if parsed.scheme:
        candidate = Path(parsed.path or "").name
    else:
        candidate = Path(source).name

    if not candidate:
        candidate = _SANITIZE_FILENAME_PATTERN.sub("_", source).strip("_")

    candidate = candidate or "remote_asset"

    sanitized = Path(candidate).name
    if not sanitized:
        sanitized = "remote_asset"

    if not Path(sanitized).suffix:
        sanitized = f"{sanitized}.tmp"

    return sanitized


def _default_asset_service() -> AssetService:
    from .storage import AssetService as _AssetService

    return _AssetService()


def _allocate_destination(storage_root: Path, filename: str) -> Path:
    candidate = storage_root / filename
    if not candidate.exists():
        return candidate

    original = Path(filename)
    stem = original.stem
    suffix = original.suffix
    counter = 1

    while True:
        candidate = storage_root / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _resolve_plugin_destination(destination: Path, metadata: Mapping[str, Any]) -> Path:
    managed_hint = metadata.get("managed_path")
    if managed_hint:
        candidate = Path(str(managed_hint))
        if not candidate.is_absolute():
            candidate = destination.parent / candidate
        return candidate
    return destination


def _normalise_extension(extension: Any) -> str:
    if not extension:
        return ""

    ext = str(extension).strip()
    if not ext:
        return ""
    if not ext.startswith("."):
        ext = f".{ext}"
    return ext.lower()


def _extract_format_metadata(path: Path, extension: str) -> dict[str, Any]:
    extractor = _FORMAT_EXTRACTORS.get(extension)
    if extractor is None:
        return {}

    try:
        return extractor(path)
    except Exception:  # pragma: no cover - defensive safety net
        logger.exception("Failed to extract %s metadata for %s", extension, path)
        return {}


def load_trimesh_mesh(path: Path):
    if trimesh is None:  # pragma: no cover - dependency enforced at runtime
        logger.warning("trimesh is unavailable; unable to load mesh for %s", path)
        return None

    mesh = trimesh.load(path, force="mesh")  # type: ignore[call-arg]

    if isinstance(mesh, trimesh.Scene):  # type: ignore[attr-defined]
        if not mesh.geometry:
            return None
        mesh = trimesh.util.concatenate(tuple(mesh.geometry.values()))  # type: ignore[assignment]

    if not isinstance(mesh, trimesh.Trimesh):  # type: ignore[attr-defined]
        return None

    return mesh


def _extract_trimesh_metadata(path: Path) -> dict[str, Any]:
    mesh = load_trimesh_mesh(path)
    if mesh is None or mesh.vertices is None or mesh.faces is None:
        return {}

    vertices = mesh.vertices
    faces = mesh.faces

    if not len(vertices) or not len(faces):
        return {}

    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)

    return {
        "vertex_count": int(len(vertices)),
        "face_count": int(len(faces)),
        "bounding_box_min": [float(value) for value in bbox_min[:3]],
        "bounding_box_max": [float(value) for value in bbox_max[:3]],
        "units": getattr(mesh, "units", "unspecified") or "unspecified",
    }


_STEP_POINT_RE = re.compile(r"CARTESIAN_POINT\(['\w-]*',\s*\(([^)]*)\)\)")
_STEP_UNIT_RE = re.compile(r"SI_UNIT\(([^)]*)\)", re.IGNORECASE)


def extract_step_metadata(path: Path) -> dict[str, Any]:
    try:
        payload = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}

    metadata: dict[str, Any] = {}
    matches = _STEP_POINT_RE.finditer(payload)
    points = [_parse_step_point(match.group(1)) for match in matches]
    points = [point for point in points if point is not None]

    if points:
        xs, ys, zs = zip(*points, strict=False)
        metadata["vertex_count"] = len(points)
        metadata["bounding_box_min"] = [
            round(float(min(xs)), 6),
            round(float(min(ys)), 6),
            round(float(min(zs)), 6),
        ]
        metadata["bounding_box_max"] = [
            round(float(max(xs)), 6),
            round(float(max(ys)), 6),
            round(float(max(zs)), 6),
        ]
        metadata.setdefault("face_count", 0)

    unit = _parse_step_unit(payload)
    if unit:
        metadata["units"] = unit
    elif "units" not in metadata:
        metadata["units"] = "unspecified"

    return metadata


def _parse_step_point(raw: str | None) -> tuple[float, float, float] | None:
    if not raw:
        return None

    components = [component.strip() for component in raw.split(",")]
    if len(components) < 3:
        return None

    parsed: list[float] = []
    for element in components[:3]:
        normalized = element.replace("D", "E").replace("d", "E")
        try:
            parsed.append(float(normalized))
        except ValueError:
            return None

    return tuple(parsed)  # type: ignore[return-value]


_STEP_PREFIX_MAP: dict[str, str] = {
    "ATTO": "attometre",
    "CENTI": "centimetre",
    "DECI": "decimetre",
    "DEKA": "dekametre",
    "EXA": "exametre",
    "FEMTO": "femtometre",
    "GIGA": "gigametre",
    "HECTO": "hectometre",
    "KILO": "kilometre",
    "MEGA": "megametre",
    "MICRO": "micrometre",
    "MILLI": "millimetre",
    "NANO": "nanometre",
    "PETA": "petametre",
    "PICO": "picometre",
    "TERA": "terametre",
    "YOCTO": "yoctometre",
    "YOTTA": "yottametre",
    "ZEPTO": "zeptometre",
    "ZETTA": "zettametre",
}


def _parse_step_unit(payload: str) -> str | None:
    for match in _STEP_UNIT_RE.finditer(payload):
        content = match.group(1)
        if not content:
            continue
        parts = [part.strip() for part in content.split(",") if part.strip()]
        if len(parts) < 2:
            continue

        prefix_raw, unit_raw = parts[0], parts[1]
        if prefix_raw == "$":
            prefix_raw = ""

        prefix = prefix_raw.strip(".").upper()
        unit = unit_raw.strip(".").upper()

        if unit != "METRE":
            label = unit.lower()
            if prefix:
                label = f"{prefix.lower()} {label}"
            return label
        if not prefix:
            return "metre"

        formatted = _STEP_PREFIX_MAP.get(prefix)
        if formatted:
            return formatted
        return f"{prefix.lower()}metre"

    return None


_Extractor = Callable[[Path], dict[str, Any]]

_FORMAT_EXTRACTORS: dict[str, _Extractor] = {
    ".stl": _extract_trimesh_metadata,
    ".obj": _extract_trimesh_metadata,
    ".step": extract_step_metadata,
    ".stp": extract_step_metadata,
}
