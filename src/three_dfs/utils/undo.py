"""Simple undo/redo tracking for reversible file operations."""

from __future__ import annotations

import base64
import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import get_config

_DEFAULT_HISTORY_FILENAME = ".3dfs-history.json"
_DEFAULT_TRASH_FOLDER = ".3dfs-trash"


@dataclass(slots=True)
class UndoAction:
    """Representation of a single reversible action."""

    kind: str
    payload: dict[str, Any]
    description: str
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class ActionHistory:
    """Persist a stack of reversible actions to disk."""

    def __init__(
        self,
        history_path: Path | None = None,
        *,
        trash_root: Path | None = None,
        max_entries: int = 50,
        use_versions: bool = True,
    ) -> None:
        self._history_path = history_path or (get_config().library_root / _DEFAULT_HISTORY_FILENAME)
        self._trash_root = trash_root or (get_config().library_root / _DEFAULT_TRASH_FOLDER)
        self._max_entries = max_entries
        self._use_versions = use_versions
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        self._trash_root.mkdir(parents=True, exist_ok=True)
        self._undo_stack: list[UndoAction]
        self._redo_stack: list[UndoAction]
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def trash_root(self) -> Path:
        """Return the folder used to hold reversible deletions."""

        return self._trash_root

    @property
    def uses_versions(self) -> bool:
        """True when deletion history is backed by container versions."""

        return self._use_versions

    def record_action(self, action: UndoAction) -> None:
        """Push *action* onto the undo stack and persist to disk."""

        self._undo_stack.append(action)
        # Trim to the most recent ``max_entries`` to avoid unbounded growth.
        if len(self._undo_stack) > self._max_entries:
            overflow = len(self._undo_stack) - self._max_entries
            if overflow > 0:
                self._undo_stack = self._undo_stack[overflow:]
        # Any new action invalidates the redo history.
        self._redo_stack.clear()
        self._save()

    def record_deletion(
        self,
        *,
        kind: str,
        original_path: Path,
        trash_path: Path | None,
        container_asset_id: int | None,
        container_asset_path: str | None,
        container_metadata: dict[str, Any] | None,
        asset_snapshot: dict[str, Any] | None,
        file_bytes: bytes | None = None,
        asset_service=None,
    ) -> None:
        """Capture a reversible deletion event on disk and in metadata."""

        container_version_id: int | None = None
        if self._use_versions and asset_service is not None and container_asset_id is not None:
            container_version_id = self._create_hidden_version(
                asset_service,
                container_asset_id,
                container_metadata,
            )

        payload: dict[str, Any] = {
            "kind": kind,
            "original_path": str(original_path),
            "trash_path": str(trash_path) if trash_path else None,
            "container_asset_id": container_asset_id,
            "container_asset_path": container_asset_path,
            "container_metadata": container_metadata,
            "asset_snapshot": asset_snapshot,
            "container_version_id": container_version_id,
            "file_contents": base64.b64encode(file_bytes).decode("ascii") if file_bytes is not None else None,
        }
        description = f"Removed {original_path.name}"
        self.record_action(UndoAction(kind="delete_entry", payload=payload, description=description))

    def trash_file(self, path: Path) -> Path:
        """Move *path* into the trash folder and return the new location."""

        self._trash_root.mkdir(parents=True, exist_ok=True)
        target = self._trash_root / f"{path.name}.{datetime.utcnow().timestamp():.0f}"
        target = target.with_suffix(path.suffix + target.suffix)
        shutil.move(str(path), target)
        return target

    def undo_last(self, *, asset_service) -> str | None:
        """Undo the most recent action using *asset_service* for persistence."""

        if not self._undo_stack:
            return None

        action = self._undo_stack.pop()
        message: str | None = None
        if action.kind == "delete_entry":
            message = self._restore_deleted_entry(action.payload, asset_service)
        else:
            message = f"No undo handler for '{action.kind}'"

        # Only push to redo stack if the action was handled.
        if message is not None:
            self._redo_stack.append(action)
            self._save()
        return message

    def redo_last(self, *, asset_service) -> str | None:
        """Re-apply the most recent undone action."""

        if not self._redo_stack:
            return None
        action = self._redo_stack.pop()
        message: str | None = None
        if action.kind == "delete_entry":
            message = self._reapply_deleted_entry(action.payload, asset_service)
        else:
            message = f"No redo handler for '{action.kind}'"

        if message is not None:
            self._undo_stack.append(action)
            self._save()
        return message

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _create_hidden_version(
        self,
        asset_service,
        container_asset_id: int,
        container_metadata: dict[str, Any] | None,
    ) -> int | None:
        """Persist a hidden container version to back the undo payload."""

        if container_metadata is None:
            return None

        from ..storage import AssetService  # Local import to avoid cycles in type checkers
        from ..storage.service import UNDO_VERSION_NAME_PREFIX, UNDO_VERSION_NOTE

        if not isinstance(asset_service, AssetService):  # pragma: no cover - defensive
            return None

        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
        name = f"{UNDO_VERSION_NAME_PREFIX}{timestamp}"
        try:
            record = asset_service.create_container_version(
                int(container_asset_id),
                name=name,
                metadata=container_metadata,
                notes=UNDO_VERSION_NOTE,
            )
        except Exception:
            return None

        self._prune_hidden_versions(asset_service, int(container_asset_id))
        return int(record.id)

    def _prune_hidden_versions(self, asset_service, container_asset_id: int) -> None:
        """Trim hidden undo snapshots beyond the configured history length."""

        from ..storage import AssetService  # Local import to avoid cycles in type checkers

        if not isinstance(asset_service, AssetService):  # pragma: no cover - defensive
            return

        try:
            versions = asset_service.list_all_container_versions(container_asset_id)
        except Exception:
            return

        hidden_versions = [record for record in versions if asset_service.is_hidden_undo_version(record)]
        excess = len(hidden_versions) - self._max_entries
        if excess <= 0:
            return

        hidden_versions.sort(key=lambda record: record.created_at)
        for record in hidden_versions[:excess]:
            try:
                asset_service.delete_container_version(record.id)
            except Exception:  # pragma: no cover - best effort cleanup
                continue

    def _restore_deleted_entry(self, payload: dict[str, Any], asset_service) -> str | None:
        from ..storage import AssetService  # Local import to avoid cycles in type checkers

        if not isinstance(asset_service, AssetService):  # pragma: no cover - defensive
            return None

        original_path = Path(payload.get("original_path") or "")
        trash_value = payload.get("trash_path")
        trash_path = Path(trash_value) if isinstance(trash_value, str) else None
        metadata = payload.get("container_metadata")
        container_asset_id = payload.get("container_asset_id")
        container_asset_path = payload.get("container_asset_path")
        asset_snapshot = payload.get("asset_snapshot")
        container_version_id = payload.get("container_version_id")
        file_contents_encoded = payload.get("file_contents")

        if container_version_id is not None:
            version = asset_service.get_container_version(int(container_version_id))
            if version is not None:
                metadata = version.metadata

        if trash_path and trash_path.exists():
            original_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(trash_path), original_path)
        elif isinstance(file_contents_encoded, str) and original_path:
            try:
                contents = base64.b64decode(file_contents_encoded.encode("ascii"))
            except Exception:
                contents = None
            if contents is not None:
                original_path.parent.mkdir(parents=True, exist_ok=True)
                original_path.write_bytes(contents)

        if asset_snapshot is not None:
            snapshot_path = asset_snapshot.get("path")
            if snapshot_path:
                existing = asset_service.get_asset_by_path(snapshot_path)
                if existing is None:
                    asset_service.create_asset(
                        snapshot_path,
                        label=asset_snapshot.get("label") or snapshot_path,
                        metadata=asset_snapshot.get("metadata") or {},
                        tags=asset_snapshot.get("tags") or [],
                    )
                else:
                    asset_service.update_asset(
                        existing.id,
                        label=asset_snapshot.get("label") or existing.label,
                        metadata=asset_snapshot.get("metadata") or existing.metadata,
                        tags=asset_snapshot.get("tags") or existing.tags,
                    )

        if metadata is not None:
            container = None
            if container_asset_id is not None:
                container = asset_service.get_asset(int(container_asset_id))
            if container is None and container_asset_path:
                container = asset_service.get_asset_by_path(container_asset_path)
            if container is not None:
                asset_service.update_asset(container.id, metadata=metadata)

        return f"Restored {original_path.name}" if original_path.name else "Restored entry"

    def _reapply_deleted_entry(self, payload: dict[str, Any], asset_service) -> str | None:
        from ..storage import AssetService  # Local import to avoid cycles in type checkers

        if not isinstance(asset_service, AssetService):  # pragma: no cover - defensive
            return None

        original_path = Path(payload.get("original_path") or "")
        trash_value = payload.get("trash_path")
        trash_path = Path(trash_value) if isinstance(trash_value, str) else None
        metadata_before = payload.get("container_metadata")
        container_asset_id = payload.get("container_asset_id")
        container_asset_path = payload.get("container_asset_path")
        asset_snapshot = payload.get("asset_snapshot")
        container_version_id = payload.get("container_version_id")
        file_contents_encoded = payload.get("file_contents")

        if container_version_id is not None:
            version = asset_service.get_container_version(int(container_version_id))
            if version is not None:
                metadata_before = version.metadata

        if original_path.exists():
            try:
                original_path.unlink()
            except OSError:
                pass
        elif trash_path is not None and trash_path.exists():
            trash_path.unlink(missing_ok=True)

        if isinstance(asset_snapshot, dict):
            snapshot_path = asset_snapshot.get("path")
            existing = asset_service.get_asset_by_path(snapshot_path) if snapshot_path else None
            if existing is not None:
                asset_service.delete_asset(existing.id)

        if isinstance(file_contents_encoded, str):
            try:
                file_contents = base64.b64decode(file_contents_encoded.encode("ascii"))
            except Exception:
                file_contents = None
            if file_contents is not None:
                payload["file_contents"] = base64.b64encode(file_contents).decode("ascii")

        container = None
        if metadata_before is not None:
            if container_asset_id is not None:
                container = asset_service.get_asset(int(container_asset_id))
            if container is None and container_asset_path:
                container = asset_service.get_asset_by_path(container_asset_path)
            if container is not None and isinstance(container.metadata, dict):
                updated_metadata = self._remove_path_from_metadata(container.metadata, str(original_path))
                asset_service.update_asset(container.id, metadata=updated_metadata)

        return f"Re-applied deletion for {original_path.name}" if original_path.name else "Re-applied deletion"

    @staticmethod
    def _remove_path_from_metadata(metadata: dict[str, Any], path: str) -> dict[str, Any]:
        updated: dict[str, Any] = {}
        for key, value in metadata.items():
            if isinstance(value, list):
                filtered: list[Any] = []
                for entry in value:
                    if isinstance(entry, dict) and str(entry.get("path") or "") == path:
                        continue
                    if isinstance(entry, str) and entry == path:
                        continue
                    filtered.append(entry)
                if filtered:
                    updated[key] = filtered
            elif isinstance(value, dict) and str(value.get("path") or "") == path:
                continue
            else:
                updated[key] = value
        return updated

    def _load(self) -> None:
        if not self._history_path.exists():
            self._undo_stack = []
            self._redo_stack = []
            return

        try:
            raw = json.loads(self._history_path.read_text())
        except Exception:
            self._undo_stack = []
            self._redo_stack = []
            return

        self._undo_stack = [UndoAction(**entry) for entry in raw.get("undo", [])]
        self._redo_stack = [UndoAction(**entry) for entry in raw.get("redo", [])]

    def _save(self) -> None:
        payload = {
            "undo": [asdict(action) for action in self._undo_stack],
            "redo": [asdict(action) for action in self._redo_stack],
        }
        self._history_path.write_text(json.dumps(payload, indent=2))
