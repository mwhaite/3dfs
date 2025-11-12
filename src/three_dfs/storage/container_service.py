"""Service layer for managing containers."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .repository import AssetRecord

if TYPE_CHECKING:  # pragma: no cover - imported only for typing
    from .service import AssetService

__all__ = ["ContainerService"]

logger = logging.getLogger(__name__)


class ContainerService:
    """Service for managing containers."""

    def __init__(self, asset_service: AssetService) -> None:
        self._asset_service = asset_service

    def create_container(
        self,
        name: str,
        *,
        root: Path | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[AssetRecord, Path]:
        """Create a new on-disk container folder and associated asset record."""
        if root is None:
            from ..config import get_config

            base_root = get_config().library_root
        else:
            base_root = Path(root)

        base_root = base_root.expanduser()
        base_root.mkdir(parents=True, exist_ok=True)

        candidate = base_root / str(uuid.uuid4())
        attempts = 0
        while candidate.exists():
            candidate = base_root / str(uuid.uuid4())
            attempts += 1
            if attempts > 100:
                raise RuntimeError("Unable to allocate unique container path")

        candidate.mkdir(parents=True, exist_ok=False)

        asset_meta = {
            "kind": "container",
            "display_name": name,
            "components": [],
            "links": [],
            "files": [],
            "container_path": str(candidate),
        }
        if metadata:
            asset_meta.update(metadata)

        container_asset = self._asset_service.create_asset(
            str(candidate),
            label=name,
            metadata=asset_meta,
        )

        return container_asset, candidate

    @staticmethod
    def _container_display_name(asset: AssetRecord) -> str:
        metadata = asset.metadata or {}
        label = metadata.get("display_name") or asset.label or Path(asset.path).name
        return str(label)

    @staticmethod
    def _coerce_mapping_entries(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, Mapping):
            return [dict(value)]
        if isinstance(value, list):
            return [dict(entry) for entry in value if isinstance(entry, Mapping)]
        return []

    def link_containers(
        self,
        source_container: AssetRecord,
        target_container: AssetRecord,
        *,
        link_type: str = "customization",
        target_version_id: int | None = None,
    ) -> tuple[AssetRecord, AssetRecord]:
        """Create reciprocal metadata links between containers.

        When *target_version_id* is provided the link references that specific
        snapshot.  When omitted, the most recent version is used if available.
        """

        if source_container.id == target_container.id:
            return source_container, target_container

        link_id = str(uuid.uuid4())
        source_label = self._container_display_name(source_container)
        target_label = self._container_display_name(target_container)

        version = None
        if target_version_id is not None:
            version = self._asset_service.get_container_version(target_version_id)
            if version is None or version.container_asset_id != target_container.id:
                raise ValueError("Target version does not belong to selected container")
        else:
            version = self._asset_service.get_latest_container_version(target_container.id)
        version_payload: dict[str, Any] = {}
        if version is not None:
            version_payload = {
                "target_version_id": version.id,
                "target_version_name": version.name,
                "target_version_created_at": version.created_at.isoformat(),
            }

        source_metadata = dict(source_container.metadata or {})
        target_metadata = dict(target_container.metadata or {})

        outgoing_entry = {
            "path": target_container.path,
            "target_path": target_container.path,
            "label": target_label,
            "kind": "link",
            "link_id": link_id,
            "target_container_id": target_container.id,
            "metadata": {
                "link_id": link_id,
                "link_type": link_type,
                "link_target": target_container.path,
                "link_direction": "outgoing",
                "target_container_id": target_container.id,
                "source_container_id": source_container.id,
            },
            "asset_id": target_container.id,
        }
        outgoing_entry.update(version_payload)
        if version_payload:
            outgoing_entry["metadata"].update(version_payload)

        links = self._coerce_mapping_entries(source_metadata.get("links"))
        links.append(outgoing_entry)
        source_metadata["links"] = links

        incoming_entry = {
            "link_id": link_id,
            "source_container_id": source_container.id,
            "source_path": source_container.path,
            "source_label": source_label,
            "link_type": link_type,
        }
        incoming_entry.update(version_payload)

        linked_from = self._coerce_mapping_entries(target_metadata.get("linked_from"))
        linked_from.append(incoming_entry)
        target_metadata["linked_from"] = linked_from

        updated_source = self._asset_service.update_asset(
            source_container.id,
            metadata=source_metadata,
        )
        updated_target = self._asset_service.update_asset(
            target_container.id,
            metadata=target_metadata,
        )

        return (
            updated_source or source_container,
            updated_target or target_container,
        )

    def refresh_link_references(self, container: AssetRecord) -> None:
        """Update link metadata in other containers that reference *container*."""

        label = self._container_display_name(container)
        path = container.path

        for asset in self._asset_service.list_assets():
            metadata = asset.metadata or {}
            changed = False

            links = metadata.get("links")
            if isinstance(links, list) and links:
                updated_links: list[dict[str, Any]] = []
                for entry in links:
                    if not isinstance(entry, dict):
                        updated_links.append(entry)
                        continue
                    link_target_id = entry.get("target_container_id")
                    if link_target_id == container.id:
                        entry = dict(entry)
                        entry["label"] = label
                        entry["path"] = path
                        entry["target_path"] = path
                        meta = dict(entry.get("metadata") or {})
                        meta["link_target"] = path
                        meta["target_container_id"] = container.id
                        entry["metadata"] = meta
                        changed = True
                    updated_links.append(entry)
                if changed:
                    metadata["links"] = updated_links

            linked_from = metadata.get("linked_from")
            if isinstance(linked_from, list) and linked_from:
                updated_linked_from: list[dict[str, Any]] = []
                linked_changed = False
                for entry in linked_from:
                    if not isinstance(entry, dict):
                        updated_linked_from.append(entry)
                        continue
                    if entry.get("source_container_id") == container.id:
                        entry = dict(entry)
                        entry["source_label"] = label
                        entry["source_path"] = path
                        linked_changed = True
                    updated_linked_from.append(entry)
                if linked_changed:
                    metadata["linked_from"] = updated_linked_from
                    changed = True

            if changed:
                self._asset_service.update_asset(asset.id, metadata=metadata)

    def find_container_for_asset(self, asset: AssetRecord) -> AssetRecord | None:
        """Find the container for a given asset."""
        current_path = Path(asset.path).parent
        while True:
            if not current_path.exists() or current_path == current_path.parent:
                return None

            container_asset = self._asset_service.get_asset_by_path(str(current_path))
            if container_asset and container_asset.metadata.get("kind") == "container":
                return container_asset

            current_path = current_path.parent
