"""Background assembly scanning helpers used by the Qt application."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from ..assembly import (
    build_attachment_metadata,
    build_component_metadata,
    build_placeholder_metadata,
    discover_arrangement_scripts,
)
from ..importer import SUPPORTED_EXTENSIONS
from ..storage import AssetRecord, AssetService

logger = logging.getLogger(__name__)

__all__ = [
    "AssemblyRefreshRequest",
    "AssemblyScanOutcome",
    "AssemblyScanWorker",
    "AssemblyScanWorkerSignals",
    "scan_assembly_folder",
]


@dataclass(slots=True)
class AssemblyRefreshRequest:
    """Describe follow-up actions after refreshing an assembly."""

    select_in_repo: bool = False
    show_assembly: bool = False
    focus_component: str | None = None


@dataclass(slots=True)
class AssemblyScanOutcome:
    """Result produced by :class:`AssemblyScanWorker`."""

    folder: Path
    asset: AssetRecord
    component_count: int


class AssemblyScanWorkerSignals(QObject):
    """Signals emitted by :class:`AssemblyScanWorker`."""

    finished = Signal(object)
    error = Signal(str, str)


class AssemblyScanWorker(QRunnable):
    """Background task that scans an assembly folder and updates metadata."""

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
        self.signals = AssemblyScanWorkerSignals()

    def run(self) -> None:  # pragma: no cover - exercised indirectly
        try:
            outcome = scan_assembly_folder(
                self._folder, self._asset_service, self._existing
            )
        except Exception as exc:  # noqa: BLE001 - safety net mirrors previous behaviour
            logger.exception("Failed to refresh assembly at %s", self._folder)
            message = str(exc) or exc.__class__.__name__
            self.signals.error.emit(str(self._folder), message)
        else:
            self.signals.finished.emit(outcome)


def scan_assembly_folder(
    folder: Path,
    asset_service: AssetService,
    existing: AssetRecord | None,
) -> AssemblyScanOutcome:
    """Return refreshed metadata for *folder* and persist it."""

    folder = folder.expanduser().resolve()
    name = folder.name
    label = f"Assembly: {name}"

    components: list[dict[str, Any]] = []
    parts_with_models: set[str] = set()
    for path in folder.rglob("*"):
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
        parts_with_models.add(parent_dir)
        try:
            parent = Path(record.path).parent
            comp_label = parent.name if parent != folder else Path(record.path).stem
        except Exception:  # noqa: BLE001 - keep behaviour consistent with legacy logic
            comp_label = record.label
        comp_metadata = build_component_metadata(record, assembly_root=folder)
        components.append(
            {
                "path": record.path,
                "label": comp_label,
                "kind": "component",
                "asset_id": record.id,
                "metadata": comp_metadata,
            }
        )

    try:
        for sub in sorted([p for p in folder.iterdir() if p.is_dir()]):
            if sub.name.startswith("."):
                continue
            if str(sub) in parts_with_models:
                continue
            components.append(
                {
                    "path": str(sub),
                    "label": sub.name,
                    "kind": "placeholder",
                    "metadata": build_placeholder_metadata(sub, assembly_root=folder),
                }
            )
    except Exception:  # noqa: BLE001 - filesystem access is inherently fallible
        pass

    preserved_attachments: list[dict[str, Any]] = []
    preserved_arrangements: list[dict[str, Any]] = []
    existing_metadata = (
        dict(existing.metadata)
        if existing is not None and isinstance(existing.metadata, dict)
        else {}
    )
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
                assembly_root=folder,
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
            "kind": "assembly",
            "components": components,
            "project": name,
        }
    )
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

    return AssemblyScanOutcome(
        folder=folder,
        asset=asset,
        component_count=len(components),
    )
