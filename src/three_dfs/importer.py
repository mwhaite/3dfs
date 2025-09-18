"""Utilities for importing external 3D assets into managed storage."""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

try:  # pragma: no cover - import guard exercised via tests
    import trimesh
except ImportError:  # pragma: no cover - dependency guaranteed in production
    trimesh = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from .storage import AssetRecord, AssetService

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "AssetImportError",
    "UnsupportedAssetTypeError",
    "import_asset",
    "load_trimesh_mesh",
    "extract_step_metadata",
]

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".stl",
        ".obj",
        ".step",
        ".stp",
    }
)
"""Supported file extensions for imported assets."""

DEFAULT_STORAGE_ROOT: Final[Path] = Path.home() / ".3dfs" / "assets" / "imports"
"""Default directory where imported assets are stored."""


class AssetImportError(RuntimeError):
    """Base exception raised when an asset cannot be imported."""


class UnsupportedAssetTypeError(AssetImportError):
    """Raised when attempting to import an unsupported asset format."""


def import_asset(
    path: Path,
    *,
    service: AssetService | None = None,
    storage_root: Path | None = None,
) -> AssetRecord:
    """Import the asset located at *path* into managed storage.

    Parameters
    ----------
    path:
        The filesystem path to the asset to import.
    service:
        Optional :class:`~three_dfs.storage.AssetService` used to register the
        asset. A new service instance is created when omitted.
    storage_root:
        Directory where managed copies of imported assets are persisted. When
        omitted the importer uses :data:`DEFAULT_STORAGE_ROOT`.

    Returns
    -------
    AssetRecord
        The newly registered asset record.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist on disk.
    AssetImportError
        If *path* is not a file or has an unsupported extension.
    """

    source = Path(path).expanduser()
    try:
        source = source.resolve(strict=True)
    except FileNotFoundError as exc:  # pragma: no cover - value re-raised unchanged
        raise FileNotFoundError(f"Asset {path!s} does not exist") from exc

    if not source.is_file():
        raise AssetImportError(f"Asset {source!s} is not a file")

    extension = source.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedAssetTypeError(
            f"Unsupported asset format '{extension or 'unknown'}'"
        )

    managed_root = Path(storage_root or DEFAULT_STORAGE_ROOT).expanduser()
    managed_root.mkdir(parents=True, exist_ok=True)
    destination = _allocate_destination(managed_root, source.name)

    shutil.copy2(source, destination)
    imported_at = datetime.now(UTC).isoformat()
    metadata = {
        "original_path": str(source),
        "managed_path": str(destination),
        "extension": extension.lstrip(".").upper(),
        "size": destination.stat().st_size,
        "imported_at": imported_at,
    }

    metadata.update(_extract_format_metadata(destination, extension))

    asset_service = service or _default_asset_service()
    try:
        record = asset_service.create_asset(
            destination.as_posix(),
            label=source.stem,
            metadata=metadata,
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return record


def _default_asset_service() -> AssetService:
    """Return a lazily imported :class:`AssetService` instance."""

    from .storage import AssetService as _AssetService

    return _AssetService()


def _allocate_destination(storage_root: Path, filename: str) -> Path:
    """Return a non-conflicting destination for *filename* within *storage_root*."""

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


def _extract_format_metadata(path: Path, extension: str) -> dict[str, Any]:
    """Extract metadata for *path* based on *extension*."""

    extractor = _FORMAT_EXTRACTORS.get(extension)
    if extractor is None:
        return {}

    try:
        return extractor(path)
    except Exception:  # pragma: no cover - defensive safety net
        logger.exception("Failed to extract %s metadata for %s", extension, path)
        return {}


def load_trimesh_mesh(path: Path):
    """Load *path* into a :class:`trimesh.Trimesh` instance when possible."""

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
    """Return mesh statistics using :mod:`trimesh` for OBJ/STL models."""

    mesh = load_trimesh_mesh(path)
    if mesh is None:
        return {}

    metadata: dict[str, Any] = {}

    vertex_count = int(mesh.vertices.shape[0]) if mesh.vertices is not None else 0
    face_count = int(mesh.faces.shape[0]) if mesh.faces is not None else 0

    if vertex_count:
        metadata["vertex_count"] = vertex_count
    if face_count:
        metadata["face_count"] = face_count

    if mesh.bounds.size:  # type: ignore[attr-defined]
        min_corner = [round(float(value), 6) for value in mesh.bounds[0]]  # type: ignore[index]
        max_corner = [round(float(value), 6) for value in mesh.bounds[1]]  # type: ignore[index]
        metadata["bounding_box_min"] = min_corner
        metadata["bounding_box_max"] = max_corner

    units = getattr(mesh, "units", None)
    metadata["units"] = str(units or "unspecified")

    return metadata


_STEP_POINT_RE = re.compile(
    r"CARTESIAN_POINT\s*\([^,]+,\s*\(([^)]+)\)\)",
    re.IGNORECASE,
)
_STEP_UNIT_RE = re.compile(r"SI_UNIT\(([^)]*)\)", re.IGNORECASE)


def extract_step_metadata(path: Path) -> dict[str, Any]:
    """Return coarse STEP metadata extracted via lightweight parsing."""

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
