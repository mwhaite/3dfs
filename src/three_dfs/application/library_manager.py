"""Library management functionality for the 3dfs desktop shell."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem

from ..config import get_config
from ..container import is_container_metadata
from ..importer import SUPPORTED_EXTENSIONS

if TYPE_CHECKING:
    from .main_window import MainWindow


logger = logging.getLogger(__name__)


class LibraryManager:
    """Handles library-related actions for the main window."""

    def __init__(self, main_window: MainWindow) -> None:
        """Initialize the library manager."""
        self._main_window = main_window

    def friendly_asset_label(self, asset) -> str:
        metadata = asset.metadata or {}

        friendly = metadata.get("display_name")
        if isinstance(friendly, str) and friendly.strip():
            return friendly.strip()
        label = asset.label if isinstance(asset.label, str) else None
        if label and label.strip():
            return label.strip()
        # Fall back to asset.id if both display_name and label are missing
        identifier = getattr(asset, "id", None)
        return f"Container {identifier}" if identifier is not None else "Container"

    def toggle_star(self, asset_id: int) -> None:
        """Toggle the 'starred' status of a container."""
        asset = self._main_window._asset_service.get_asset(asset_id)
        if not asset:
            return

        tags = set(asset.tags or [])
        if "starred" in tags:
            tags.remove("starred")
        else:
            tags.add("starred")
        self._main_window._asset_service.set_tags_for_asset(asset.id, list(tags))

        for i in range(self._main_window._repository_list.count()):
            item = self._main_window._repository_list.item(i)
            if item.data(Qt.UserRole) == asset_id:
                item.setData(Qt.UserRole + 2, list(tags))
                self._main_window._repository_list.update(self._main_window._repository_list.indexFromItem(item))
                self._main_window._tag_panel.set_active_item(asset_id)
                break

    def populate_repository(self) -> None:
        """Populate the repository view with persisted asset entries."""

        config = get_config()
        library_root = config.library_root
        try:
            pruned = self._main_window._asset_service.prune_missing_assets(base_path=library_root)
        except Exception:  # noqa: BLE001 - pruning should not block UI
            logger.exception("Failed to prune missing assets")
            pruned = 0

        self._main_window._repository_list.clear()
        # By default show only persisted assets; opt-in demo seeding via settings.
        if self._main_window._bootstrap_demo_data:
            all_assets = self._main_window._asset_service.bootstrap_demo_data()
        else:
            all_assets = self._main_window._asset_service.list_assets()

        assets = []
        for asset in all_assets:
            metadata = asset.metadata or {}
            if not is_container_metadata(metadata):
                continue
            asset_path = Path(asset.path).expanduser()
            if not asset_path.is_dir():
                continue
            assets.append((asset, metadata))

        valid_assets = 0
        root_resolved = library_root.expanduser().resolve()
        for asset, _metadata in assets:
            # DEBUG: Log the raw asset path to find the source of corruption
            logger.debug(
                "Processing asset: id=%s, path=%r, path_type=%s, path_len=%d",
                asset.id,
                asset.path,
                type(asset.path),
                len(asset.path),
            )

            # FUNDAMENTAL FIX: Validate asset paths before adding to UI
            if not self._main_window._is_safe_path_string(asset.path):
                # CRITICAL: Don't log the corrupted path directly as it causes recursion
                try:
                    safe_sample = repr(asset.path[:100]) if len(asset.path) > 100 else repr(asset.path)
                    print(
                        f"CORRUPTED ASSET PATH: id={asset.id}, len={len(asset.path)}, sample={safe_sample}",
                        flush=True,
                    )
                except Exception:
                    print(
                        f"CORRUPTED ASSET PATH: id={asset.id}, len={len(asset.path)}, repr failed",
                        flush=True,
                    )
                continue
            try:
                asset_resolved = Path(asset.path).expanduser().resolve()
            except Exception:
                asset_resolved = None
            else:
                if asset_resolved == root_resolved:
                    # Don't surface the library root as a container entry.
                    continue

            display_label = self.friendly_asset_label(asset)
            item = QListWidgetItem(display_label)
            item.setData(Qt.UserRole, asset.id)
            item.setData(Qt.UserRole + 1, asset.path)
            item.setToolTip(asset.path)
            item.setData(Qt.UserRole + 2, asset.tags)
            self._main_window._repository_list.addItem(item)
            valid_assets += 1

        if self._main_window._repository_list.count():
            self._main_window._repository_list.setCurrentRow(0)
        else:
            # Surface the current library root to help users locate files.
            self._main_window.statusBar().showMessage(f"Library: {library_root}", 5000)

        status_bits: list[str] = []
        if pruned:
            plural = "s" if pruned != 1 else ""
            status_bits.append(f"removed {pruned} missing asset{plural}")
        if valid_assets < len(assets):
            skipped = len(assets) - valid_assets
            plural = "s" if skipped != 1 else ""
            status_bits.append(f"skipped {skipped} invalid path{plural}")

        if status_bits:
            self._main_window.statusBar().showMessage(
                f"Loaded {valid_assets} assets; {'; '.join(status_bits)}",
                3000,
            )

    def apply_library_filters(self) -> None:
        raw_text = (
            self._main_window._repo_search_input.text() if hasattr(self._main_window, "_repo_search_input") else ""
        )
        query = raw_text.strip()
        override_tags = [term[1:].strip() for term in query.split() if term.startswith("#")]
        plain_queries = [term for term in query.split() if not term.startswith("#")]
        query = " ".join(plain_queries)

        text_needle = query.casefold()
        search_paths = self.run_library_search(query) if query else None

        tag_ids: set[int] | None = None
        if override_tags:
            try:
                tagged_paths_per_tag = [
                    set(self._main_window._asset_service.paths_for_tag(tag)) for tag in override_tags
                ]
                tagged_paths = set.intersection(*tagged_paths_per_tag) if tagged_paths_per_tag else set()
            except Exception:
                tagged_paths = set()

            tag_ids = set()
            for raw_path in tagged_paths:
                asset = self._main_window._asset_service.get_asset_by_path(raw_path)
                container_path = None
                if asset is not None and isinstance(asset.metadata, dict):
                    metadata = asset.metadata
                    container_candidate = metadata.get("container_path")
                    if isinstance(container_candidate, str) and container_candidate.strip():
                        container_path = container_candidate.strip()
                if container_path is None:
                    try:
                        container_path = str(Path(raw_path).expanduser().resolve().parent)
                    except Exception:
                        container_path = None
                if not container_path:
                    continue
                container_asset = self._main_window._asset_service.get_asset_by_path(container_path)
                if container_asset is None:
                    continue
                try:
                    container_id = int(container_asset.id)
                    tag_ids.add(container_id)
                except Exception:
                    continue

        for row in range(self._main_window._repository_list.count()):
            item = self._main_window._repository_list.item(row)
            raw_path = item.data(Qt.UserRole + 1) or item.text()
            path = str(raw_path) if raw_path is not None else ""
            label = item.text()

            visible = True
            if search_paths is not None and path not in search_paths:
                visible = False

            if text_needle:
                label_case = (label or "").casefold()
                if not all(term in label_case or term in path.casefold() for term in text_needle.split()):
                    visible = False

            if tag_ids is not None:
                try:
                    candidate_id = int(item.data(Qt.UserRole))
                except (TypeError, ValueError):
                    candidate_id = None
                if candidate_id not in tag_ids:
                    visible = False

            item.setHidden(not visible)

    def run_library_search(self, query: str) -> set[str] | None:
        """Return asset paths that match *query* using :mod:`three_dfs.search`."""
        if not query:
            return set()
        try:
            terms = query.split()
            hits_per_term = [self._main_window._library_search.search(term) for term in terms]

            if not hits_per_term:
                return set()

            paths_per_term = []
            for hits in hits_per_term:
                paths = set()
                for hit in hits:
                    target = hit.container_path or hit.path
                    if target:
                        paths.add(target)
                paths_per_term.append(paths)

            matches = set.intersection(*paths_per_term) if paths_per_term else set()
        except Exception:
            logger.exception("Failed to execute library search", exc_info=True)
            return None

        return matches

    def rescan_library(self) -> None:
        config = get_config()
        root = config.library_root.expanduser().resolve()
        if not root.exists():
            self._main_window.statusBar().showMessage("Library root does not exist on disk.", 4000)
            return

        discovered: list[Path] = []
        for entry in root.iterdir():
            if entry.is_dir():
                discovered.append(entry)

        if not discovered:
            self._main_window.statusBar().showMessage("No containers discovered under library root.", 4000)
            return

        for folder in discovered:
            self._main_window._container_manager.create_or_update_container(folder)

    def organize_library(self) -> None:
        """Group lone model files into per-container folders and update records."""

        config = get_config()
        root = config.library_root

        moved = 0
        errors = 0

        def derive_container_name(path: Path) -> str:
            stem = path.stem
            for sep in ("_", "-"):
                if sep in stem:
                    base = stem.split(sep, 1)[0].strip()
                    if base:
                        return base
            return stem

        for asset in list(self._main_window._asset_service.list_assets()):
            try:
                source = Path(asset.path)
                try:
                    relative = source.resolve().relative_to(root)
                except Exception:
                    continue

                if not source.exists():
                    continue
                if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                if relative.parent != Path("."):
                    continue

                container_name = derive_container_name(source)
                container_dir = root / container_name
                container_dir.mkdir(parents=True, exist_ok=True)

                destination = container_dir / source.name
                if destination.exists():
                    counter = 1
                    while True:
                        candidate_name = f"{source.stem}_{counter}{source.suffix}"
                        candidate = container_dir / candidate_name
                        if not candidate.exists():
                            destination = candidate
                            break
                        counter += 1

                source = source.resolve()
                destination = destination.resolve()
                source.rename(destination)

                metadata = dict(asset.metadata or {})
                metadata["container"] = container_name
                managed_path = metadata.get("managed_path")
                if managed_path:
                    try:
                        managed_resolved = Path(str(managed_path)).expanduser().resolve()
                    except Exception:
                        managed_resolved = None
                    if managed_resolved is None or managed_resolved == source:
                        metadata["managed_path"] = str(destination)
                else:
                    metadata["managed_path"] = str(destination)

                self._main_window._asset_service.update_asset(
                    asset.id,
                    path=str(destination),
                    metadata=metadata,
                )
                moved += 1
            except Exception:
                errors += 1
                continue

        self.populate_repository()
        msg = f"Organize complete: {moved} moved"
        if errors:
            msg += f", {errors} failed"
        self._main_window.statusBar().showMessage(msg, 5000)
