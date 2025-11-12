"""Background container scanning helpers used by the Qt application."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from ..importer import SUPPORTED_EXTENSIONS
from ..storage import AssetRecord, AssetService

logger = logging.getLogger(__name__)

__all__ = [
    "ContainerRefreshRequest",
    "ContainerScanOutcome",
    "ContainerScanWorker",
    "ContainerScanWorkerSignals",
    "scan_container_folder",
    "is_valid_container_folder",
]


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


def _normalize_part_key(raw_key: Any, root: Path) -> str | None:
    try:
        text = str(raw_key).strip()
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
    return normalized if normalized else None


def is_valid_container_folder(folder: Path) -> bool:
    """Return True when the directory name is a UUID."""

    try:
        uuid.UUID(folder.name)
    except (ValueError, AttributeError):
        return False
    return True


def _normalise_primary_components(raw_map: Mapping[str, Any] | Any, container_root: Path) -> dict[str, str]:
    if not isinstance(raw_map, Mapping):
        return {}
    normalised: dict[str, str] = {}
    for key, value in raw_map.items():
        part_key = _normalize_part_key(key, container_root)
        rel_path = _normalize_rel_path(value, container_root)
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


def _load_virtual_link(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None

    data: dict[str, Any]
    try:
        parsed = json.loads(raw)
        data = parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        text = raw.strip()
        data = {"target": text} if text else {}

    target_raw = data.get("target")
    if not isinstance(target_raw, str) or not target_raw.strip():
        return None

    try:
        target_resolved = Path(target_raw).expanduser().resolve()
    except Exception:
        target_resolved = Path(target_raw).expanduser()

    label_raw = data.get("label")
    label = label_raw.strip() if isinstance(label_raw, str) and label_raw.strip() else path.stem

    entry: dict[str, Any] = {
        "path": str(path),
        "target": str(target_resolved),
        "label": label,
        "link_type": data.get("link_type") or "virtual",
    }
    return entry


def _normalize_link_entries(value: object) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        entries.append(dict(value))
    elif isinstance(value, list):
        for entry in value:
            if isinstance(entry, Mapping):
                entries.append(dict(entry))
    return entries


def _link_identity(entry: Mapping[str, Any]) -> tuple[str, str] | None:
    for key in ("link_id", "path", "target_path", "label"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return key, value.strip()
    return None


def _merge_link_metadata(
    scanned_links: list[dict[str, Any]],
    preserved_links: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not preserved_links:
        return scanned_links

    merged: dict[tuple[str, str] | tuple[str, int], dict[str, Any]] = {}

    def _add(entry: Mapping[str, Any], *, prefer_existing: bool) -> None:
        normalized = dict(entry)
        identity = _link_identity(normalized)
        key: tuple[str, str] | tuple[str, int]
        if identity is None:
            key = ("index", len(merged))
        else:
            key = identity
        if prefer_existing and key in merged:
            return
        merged[key] = normalized

    for entry in scanned_links:
        _add(entry, prefer_existing=False)
    for entry in preserved_links:
        _add(entry, prefer_existing=True)

    return list(merged.values())


@dataclass(slots=True)
class ContainerRefreshRequest:
    """Describe follow-up actions after refreshing a container."""

    select_in_repo: bool = False
    show_container: bool = False
    focus_component: str | None = None
    display_name: str | None = None
    container_type: str | None = None


@dataclass(slots=True)
class ContainerScanOutcome:
    """Result produced by :class:`ContainerScanWorker`."""

    folder: Path
    asset: AssetRecord
    component_count: int


class ContainerScanWorkerSignals(QObject):
    """Signals emitted by :class:`ProjectScanWorker`."""

    finished = Signal(object)
    error = Signal(str, str)


class ContainerScanWorker(QRunnable):
    """Background task that scans a container folder and updates metadata."""

    def __init__(
        self,
        folder: Path,
        asset_service: AssetService,
        existing: AssetRecord | None = None,
        *,
        display_name: str | None = None,
        container_type: str | None = None,
    ) -> None:
        super().__init__()
        self._folder = folder
        self._asset_service = asset_service
        self._existing = existing
        self._display_name = display_name
        self._container_type = container_type
        self.signals = ContainerScanWorkerSignals()

    def run(self) -> None:  # pragma: no cover - exercised indirectly
        try:
            outcome = scan_container_folder(
                self._folder,
                self._asset_service,
                existing=self._existing,
                display_name=self._display_name,
                container_type=self._container_type,
            )
        except Exception as exc:  # noqa: BLE001 - safety net mirrors previous behaviour
            logger.exception("Failed to refresh container at %s", self._folder)
            message = str(exc) or exc.__class__.__name__
            self.signals.error.emit(str(self._folder), message)
        else:
            if outcome is not None:
                self.signals.finished.emit(outcome)


def scan_container_folder(
    folder: Path,
    asset_service: AssetService,
    existing: AssetRecord | None,
    *,
    display_name: str | None = None,
    container_type: str | None = None,
) -> ContainerScanOutcome | None:
    """Return refreshed parts for *folder* and persist them using the new UUID-based system."""

    folder = folder.expanduser().resolve()
    name = folder.name

    if not is_valid_container_folder(folder):
        logger.debug("Skipping non-UUID folder during container scan: %s", folder)
        return None

    if existing is None:
        container_asset = asset_service.create_asset(
            str(folder),
            label=display_name or name,
            metadata={
                "kind": "container",
                "container_type": container_type or "container",
                "display_name": display_name or name,
                "container_path": str(folder),
                "created_from_scan": True,
            },
        )
    else:
        container_asset = existing
        updated_metadata = dict(container_asset.metadata) if container_asset.metadata else {}
        updated_metadata.update(
            {
                "kind": "container",
                "container_type": container_type or updated_metadata.get("container_type") or "container",
                "display_name": display_name or updated_metadata.get("display_name") or name,
                "container_path": str(folder),
                "updated_from_scan": True,
            }
        )
        container_asset = asset_service.update_asset(
            container_asset.id,
            metadata=updated_metadata,
            label=display_name or container_asset.label,
        )

    files_metadata: list[dict[str, Any]] = []
    links_metadata: list[dict[str, Any]] = []
    components_metadata: list[dict[str, Any]] = []
    component_count = 0

    all_entries = sorted(folder.glob("*"))

    generated_preview_images: set[str] = set()
    for candidate in all_entries:
        if not candidate.is_file():
            continue
        suffix = candidate.suffix.casefold()
        if suffix in SUPPORTED_EXTENSIONS:
            for preview in _discover_preview_images(candidate):
                try:
                    resolved_preview = Path(preview).expanduser().resolve()
                except Exception:
                    resolved_preview = Path(preview).expanduser()
                generated_preview_images.add(str(resolved_preview))

    for path in all_entries:
        if not path.is_file():
            continue

        asset_record = asset_service.get_asset_by_path(str(path))
        if asset_record is None:
            asset_record = asset_service.create_asset(
                str(path),
                label=path.name,
                metadata={
                    "created_from_scan": True,
                    "file_size": path.stat().st_size,
                    "suffix": path.suffix,
                },
            )

        relative_path = str(path.relative_to(folder))
        file_size = path.stat().st_size
        suffix = path.suffix.casefold()

        if suffix == ".png" and str(path) in generated_preview_images:
            logger.debug("Skipping preview image %s", path)
            continue

        entry = {
            "path": str(path),
            "relative_path": relative_path,
            "asset_id": asset_record.id,
            "label": path.name,
            "file_size": file_size,
            "suffix": path.suffix,
        }

        if suffix in SUPPORTED_EXTENSIONS:
            components_metadata.append(entry)
        elif suffix == ".3dfslink":
            link_info = _load_virtual_link(path)
            if link_info:
                links_metadata.append({**entry, **link_info})
        elif path.is_symlink():
            target = path.readlink()
            links_metadata.append({**entry, "target": str(target), "link_type": "symlink"})
        else:
            files_metadata.append(entry)

        component_count += 1

    preserved_linked_components: list[dict[str, Any]] = []
    if existing is not None:
        existing_components = existing.metadata.get("components") if isinstance(existing.metadata, Mapping) else None
        if isinstance(existing_components, list):
            for entry in existing_components:
                if not isinstance(entry, dict):
                    continue
                if entry.get("kind") != "linked_component":
                    continue
                cloned = dict(entry)
                entry_meta = cloned.get("metadata")
                if isinstance(entry_meta, dict):
                    cloned["metadata"] = dict(entry_meta)
                preserved_linked_components.append(cloned)

    if preserved_linked_components:
        components_metadata.extend(preserved_linked_components)

    # Update the container asset metadata
    updated_metadata = dict(container_asset.metadata) if container_asset.metadata else {}
    preserved_links = _normalize_link_entries(existing.metadata.get("links") if existing else None)
    if preserved_links:
        links_metadata = _merge_link_metadata(links_metadata, preserved_links)

    updated_metadata.update(
        {
            "component_count": component_count,
            "components": components_metadata,
            "files": files_metadata,
            "links": links_metadata,
        }
    )

    asset = asset_service.update_asset(
        container_asset.id,
        metadata=updated_metadata,
        label=container_asset.label,
    )

    return ContainerScanOutcome(
        folder=folder,
        asset=asset,
        component_count=component_count,
    )
