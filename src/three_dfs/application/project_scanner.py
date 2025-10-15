"""Background project scanning helpers used by the Qt application."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import QObject, QRunnable, Signal

from ..importer import SUPPORTED_EXTENSIONS
from ..project import (
    build_attachment_metadata,
    build_component_metadata,
    build_placeholder_metadata,
    discover_arrangement_scripts,
)
from ..storage import AssetRecord, AssetService

logger = logging.getLogger(__name__)

__all__ = [
    "ProjectRefreshRequest",
    "ProjectScanOutcome",
    "ProjectScanWorker",
    "ProjectScanWorkerSignals",
    "scan_project_folder",
]


def _normalize_part_key(raw_key: Any, root: Path) -> str | None:
    try:
        text = str(raw_key).strip()
    except Exception:
        return None
    if not text or text == ".":
        return "."
    candidate = Path(text.replace("\\", "/"))
    if candidate.is_absolute():
        try:
            candidate = candidate.expanduser().resolve().relative_to(root)
        except Exception:
            return None
    normalized = candidate.as_posix()
    return "." if not normalized or normalized == "." else normalized


def _normalize_rel_path(raw_path: Any, root: Path) -> str | None:
    try:
        text = str(raw_path).strip()
    except Exception:
        return None
    if not text:
        return None
    candidate = Path(text.replace("\\", "/"))
    if candidate.is_absolute():
        try:
            candidate = candidate.expanduser().resolve().relative_to(root)
        except Exception:
            return None
    normalized = candidate.as_posix()
    return normalized if normalized and normalized != "." else normalized


def _relative_to_project(path: Path, project_root: Path) -> str | None:
    try:
        relative = path.expanduser().resolve().relative_to(project_root)
    except Exception:
        return None
    text = relative.as_posix()
    return "." if not text or text == "." else text


def _part_key_for_path(path: Path, project_root: Path) -> str:
    base = path if path.is_dir() else path.parent
    rel = _relative_to_project(base, project_root)
    if rel is not None:
        return rel
    try:
        return base.expanduser().resolve().as_posix()
    except Exception:
        return str(base)


def _normalise_primary_components(
    raw_map: Mapping[str, Any] | Any, project_root: Path
) -> dict[str, str]:
    if not isinstance(raw_map, Mapping):
        return {}
    normalised: dict[str, str] = {}
    for key, value in raw_map.items():
        part_key = _normalize_part_key(key, project_root)
        rel_path = _normalize_rel_path(value, project_root)
        if part_key is None or rel_path is None:
            continue
        normalised[part_key] = rel_path
    return normalised


def _discover_preview_images(model_path: Path) -> list[str]:
    candidates: list[Path] = []
    base_dir = model_path.parent
    double_ext = model_path.with_suffix(model_path.suffix + ".png")
    if double_ext.exists():
        candidates.append(double_ext)
    simple_ext = model_path.with_suffix(".png")
    if simple_ext.exists() and simple_ext not in candidates:
        candidates.append(simple_ext)
    try:
        for png in base_dir.glob(f"{model_path.stem}*.png"):
            if png not in candidates:
                candidates.append(png)
    except Exception:
        pass
    return [str(path) for path in sorted({p.resolve() for p in candidates})]


@dataclass(slots=True)
class ProjectRefreshRequest:
    """Describe follow-up actions after refreshing a project."""

    select_in_repo: bool = False
    show_project: bool = False
    focus_component: str | None = None


@dataclass(slots=True)
class ProjectScanOutcome:
    """Result produced by :class:`ProjectScanWorker`."""

    folder: Path
    asset: AssetRecord
    component_count: int


class ProjectScanWorkerSignals(QObject):
    """Signals emitted by :class:`ProjectScanWorker`."""

    finished = Signal(object)
    error = Signal(str, str)


class ProjectScanWorker(QRunnable):
    """Background task that scans a project folder and updates metadata."""

    def __init__(
        self,
        folder: Path,
        asset_service: AssetService,
        existing: AssetRecord | None = None,
    ) -> None:
        super().__init__()
        self._folder = folder
        self._asset_service = asset_service
        self._existing = existing
        self.signals = ProjectScanWorkerSignals()

    def run(self) -> None:  # pragma: no cover - exercised indirectly
        try:
            outcome = scan_project_folder(
                self._folder, self._asset_service, self._existing
            )
        except Exception as exc:  # noqa: BLE001 - safety net mirrors previous behaviour
            logger.exception("Failed to refresh project at %s", self._folder)
            message = str(exc) or exc.__class__.__name__
            self.signals.error.emit(str(self._folder), message)
        else:
            self.signals.finished.emit(outcome)


def scan_project_folder(
    folder: Path,
    asset_service: AssetService,
    existing: AssetRecord | None,
) -> ProjectScanOutcome:
    """Return refreshed metadata for *folder* and persist it."""

    folder = folder.expanduser().resolve()
    name = folder.name
    label = f"Project: {name}"

    components: list[dict[str, Any]] = []
    existing_metadata = (
        dict(existing.metadata)
        if existing is not None and isinstance(existing.metadata, dict)
        else {}
    )
    primary_components = _normalise_primary_components(
        existing_metadata.get("primary_components"), folder
    )
    seen_part_keys: set[str] = set()

    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        record = asset_service.ensure_asset(str(path), label=path.name)
        try:
            parent_dir = str(Path(record.path).parent)
        except (
            Exception
        ):  # noqa: BLE001 - defensive: metadata can contain arbitrary paths
            parent_dir = str(folder)
        try:
            parent = Path(record.path).parent
            comp_label = parent.name if parent != folder else Path(record.path).stem
        except Exception:  # noqa: BLE001 - keep behaviour consistent with legacy logic
            comp_label = record.label
        comp_metadata = build_component_metadata(record, project_root=folder)

        record_path = Path(record.path)
        part_key = _part_key_for_path(record_path, folder)
        seen_part_keys.add(part_key)
        rel_path = _relative_to_project(record_path, folder)
        if rel_path is not None:
            comp_metadata["rel_path"] = rel_path
        comp_metadata["part_key"] = part_key
        if rel_path is not None and primary_components.get(part_key) == rel_path:
            comp_metadata["is_primary_component"] = True
        elif rel_path is not None and part_key not in primary_components:
            primary_components[part_key] = rel_path
            comp_metadata["is_primary_component"] = True

        preview_images = _discover_preview_images(record_path)
        if preview_images:
            resolved_images: list[str] = []
            for img in preview_images:
                preview_path = Path(img)
                rel_preview = _relative_to_project(preview_path, folder)
                resolved_images.append(rel_preview if rel_preview is not None else str(preview_path.resolve()))
            comp_metadata["preview_images"] = resolved_images

        components.append(
            {
                "path": record.path,
                "label": comp_label,
                "kind": "component",
                "asset_id": record.id,
                "metadata": comp_metadata,
            }
        )

    models_by_part: dict[str, list[dict[str, Any]]] = {}
    for entry in components:
        if entry.get("kind") != "component":
            continue
        meta = entry.get("metadata") or {}
        part_key = meta.get("part_key")
        if not isinstance(part_key, str):
            continue
        models_by_part.setdefault(part_key, []).append(
            {
                "path": entry.get("path"),
                "label": entry.get("label"),
                "preview_images": list(meta.get("preview_images") or []),
                "is_primary": bool(meta.get("is_primary_component")),
            }
        )

    for items in models_by_part.values():
        items.sort(key=lambda m: (not m.get("is_primary", False), str(m.get("label") or "").lower()))

    try:
        for sub in sorted([p for p in folder.iterdir() if p.is_dir()]):
            if sub.name.startswith("."):
                continue
            part_key = _part_key_for_path(sub, folder)
            seen_part_keys.add(part_key)
            placeholder_metadata = build_placeholder_metadata(
                sub, project_root=folder
            )
            placeholder_metadata["part_key"] = part_key
            primary_rel = primary_components.get(part_key)
            if primary_rel:
                placeholder_metadata["primary_component_rel_path"] = primary_rel
                try:
                    candidate = (folder / Path(primary_rel)).expanduser().resolve()
                except Exception:
                    candidate = folder / Path(primary_rel)
                placeholder_metadata["primary_component_path"] = str(candidate)
            placeholder_metadata["models"] = models_by_part.get(part_key, [])
            components.append(
                {
                    "path": str(sub),
                    "label": sub.name,
                    "kind": "placeholder",
                    "metadata": placeholder_metadata,
                }
            )
    except Exception:  # noqa: BLE001 - filesystem access is inherently fallible
        pass

    for entry in components:
        meta = entry.get("metadata")
        if not isinstance(meta, dict):
            continue
        part_key = meta.get("part_key")
        if isinstance(part_key, str):
            meta["models"] = models_by_part.get(part_key, [])

    valid_primary: dict[str, str] = {}
    for part_key, rel in primary_components.items():
        if part_key not in seen_part_keys:
            continue
        target = folder if rel in (None, ".") else folder / Path(rel)
        if not target.exists():
            continue
        valid_primary[part_key] = rel if rel is not None else "."

    preserved_attachments: list[dict[str, Any]] = []
    preserved_arrangements: list[dict[str, Any]] = []
    raw_attachments = existing_metadata.get("attachments") or []
    for entry in raw_attachments:
        if not isinstance(entry, dict):
            continue
        enriched = dict(entry)
        raw_path = str(enriched.get("path") or "").strip()
        existing_meta = (
            enriched.get("metadata")
            if isinstance(enriched.get("metadata"), dict)
            else None
        )
        if raw_path:
            enriched["metadata"] = build_attachment_metadata(
                raw_path,
                project_root=folder,
                existing_metadata=existing_meta,
            )
        preserved_attachments.append(enriched)

    raw_arrangements = existing_metadata.get("arrangements") or []
    for entry in raw_arrangements:
        if isinstance(entry, dict):
            preserved_arrangements.append(dict(entry))

    try:
        arrangements = discover_arrangement_scripts(folder, preserved_arrangements)
    except Exception:  # noqa: BLE001 - fallback to existing metadata
        arrangements = [dict(entry) for entry in preserved_arrangements]

    metadata = dict(existing_metadata)
    metadata.update(
        {
            "kind": "project",
            "components": components,
            "project": name,
        }
    )
    if valid_primary:
        metadata["primary_components"] = valid_primary
    else:
        metadata.pop("primary_components", None)
    if preserved_attachments:
        metadata["attachments"] = preserved_attachments
    else:
        metadata.pop("attachments", None)
    if arrangements:
        metadata["arrangements"] = arrangements
    else:
        metadata.pop("arrangements", None)

    if existing is None:
        asset = asset_service.create_asset(
            str(folder),
            label=label,
            metadata=metadata,
        )
    else:
        asset = asset_service.update_asset(
            existing.id,
            metadata=metadata,
            label=label,
        )

    return ProjectScanOutcome(
        folder=folder,
        asset=asset,
        component_count=len(components),
    )
