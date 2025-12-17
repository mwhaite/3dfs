"""Container management functionality for the 3dfs desktop shell."""

from __future__ import annotations

import logging
import mimetypes
import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import get_config
from ..container import (
    build_attachment_metadata,
    build_linked_component_entry,
    is_container_asset,
)
from ..importer import SUPPORTED_EXTENSIONS
from .container_scanner import ContainerRefreshRequest, ContainerScanOutcome, ContainerScanWorker

if TYPE_CHECKING:
    from PySide6.QtWidgets import QFileDialog, QListWidgetItem, QMessageBox

    from ..storage import ContainerVersionRecord
    from .main_window import MainWindow


DEFAULT_README_CONTENT = """# {name}

## Description
Enter a description of this container here.

## Contents
- [ ] Item 1
- [ ] Item 2

## Notes
Add any special instructions or notes here.
"""


logger = logging.getLogger(__name__)


class ContainerManager:
    """Handles container-related actions for the main window."""

    def __init__(self, main_window: MainWindow) -> None:
        """Initialize the container manager."""
        self._main_window = main_window

    def create_or_update_container(
        self,
        folder: Path | str | None,
        *,
        select_in_repo: bool = False,
        focus_component: str | None = None,
        show_container: bool = False,
        display_name: str | None = None,
        container_type: str | None = None,
    ) -> None:
        resolved_folder: Path | None
        if folder is None:
            candidate: str | Path | None = None
            current_asset = self._main_window._current_asset
            if is_container_asset(current_asset):
                candidate = current_asset.path
            elif self._main_window._current_container_path:
                candidate = self._main_window._current_container_path
            if candidate is None:
                self._main_window.statusBar().showMessage(
                    "Select a container before refreshing.",
                    3000,
                )
                return
            resolved_folder = Path(candidate)
        else:
            resolved_folder = Path(folder)

        folder = resolved_folder.expanduser().resolve()
        config = get_config()
        root = config.library_root
        try:
            folder.relative_to(root)
        except Exception:
            self._main_window.statusBar().showMessage("Folder must be under the library root", 4000)
            return

        key = str(folder)
        request = self._main_window._container_refresh_requests.get(key)
        if request is None:
            request = ContainerRefreshRequest()
            self._main_window._container_refresh_requests[key] = request
        if select_in_repo:
            request.select_in_repo = True
        if show_container or focus_component is not None:
            request.show_container = True
        if focus_component is not None:
            request.focus_component = focus_component
        if display_name is not None:
            request.display_name = display_name
        if container_type is not None:
            request.container_type = container_type

        if key in self._main_window._container_workers:
            self._main_window._container_pending[key] = self._main_window._container_pending.get(key, 0) + 1
            return

        existing = self._main_window._asset_service.get_asset_by_path(str(folder))
        initial_display_name = request.display_name if request is not None else None
        initial_container_type = request.container_type if request is not None else None
        if initial_container_type is None and existing and isinstance(existing.metadata, dict):
            meta_container = existing.metadata.get("container_type")
            if isinstance(meta_container, str) and meta_container:
                initial_container_type = meta_container
            else:
                initial_container_type = "container"
            if request is not None:
                request.container_type = initial_container_type
        worker = ContainerScanWorker(
            folder,
            self._main_window._asset_service,
            existing,
            display_name=initial_display_name,
            container_type=initial_container_type,
        )
        worker.signals.finished.connect(self.handle_container_scan_finished)
        worker.signals.error.connect(self.handle_container_scan_error)
        self._main_window._container_workers[key] = worker
        self._main_window.statusBar().showMessage(
            f"Updating container '{folder.name}'...",
            1500,
        )
        self._main_window._thread_pool.start(worker)

    def handle_container_scan_finished(self, payload: object) -> None:
        outcome = payload if isinstance(payload, ContainerScanOutcome) else None
        if outcome is None:
            return

        key = str(outcome.folder)
        self._main_window._container_workers.pop(key, None)
        pending = self._main_window._container_pending.pop(key, 0)
        request = self._main_window._container_refresh_requests.pop(key, None)

        self._main_window._populate_repository()
        metadata = outcome.asset.metadata

        display_text = metadata.get("display_name") if isinstance(metadata, dict) else None
        if not isinstance(display_text, str) or not display_text.strip():
            display_text = self._main_window._library_manager.friendly_asset_label(outcome.asset)

        self._main_window.statusBar().showMessage(
            f"Container '{display_text}' updated with {outcome.component_count} component(s)",
            4000,
        )

        if request is not None and request.select_in_repo:
            self._main_window._suppress_history = True
            self._main_window._select_repository_path(key)
            self._main_window._suppress_history = False

        should_show = False
        focus_components: list[str] = []
        if request is not None:
            should_show = request.show_container
            if request.focus_component is not None:
                focus_components.append(request.focus_component)

        try:
            container_asset_id = int(outcome.asset.id)
        except Exception:
            container_asset_id = None

        if container_asset_id is not None:
            self._main_window._tag_filter_container_paths[container_asset_id] = outcome.asset.path

        if container_asset_id is not None:
            extra_targets = self._main_window._tag_filter_focus_map.get(container_asset_id)
            if extra_targets:
                for target in extra_targets:
                    if target not in focus_components:
                        focus_components.append(target)

        current_path = None
        if self._main_window._current_asset is not None:
            try:
                current_path = Path(self._main_window._current_asset.path).expanduser().resolve()
            except Exception:  # noqa: BLE001
                current_path = None
        if current_path == outcome.folder:
            should_show = True

        if should_show:
            self._prompt_for_readme(outcome.folder, outcome.asset)
            self._main_window._show_container(outcome.asset)
            if focus_components:
                try:
                    self._main_window._container_pane.focus_matching_item(focus_components)
                except Exception:  # noqa: BLE001
                    pass
        else:
            if self._main_window._current_asset is not None and str(self._main_window._current_asset.path) == key:
                self._main_window._current_asset = outcome.asset

        self._main_window._update_container_watchers()

        if pending > 0:
            remaining = pending - 1
            if remaining > 0:
                self._main_window._container_pending[key] = remaining
            self.create_or_update_container(outcome.folder)

    def handle_container_scan_error(self, folder_path: str, message: str) -> None:
        self._main_window._container_workers.pop(folder_path, None)
        pending = self._main_window._container_pending.pop(folder_path, 0)
        self._main_window._container_refresh_requests.pop(folder_path, None)

        folder_name = Path(folder_path).name
        self._main_window.statusBar().showMessage(
            f"Failed to update container '{folder_name}': {message}",
            5000,
        )

        if pending > 0:
            try:
                self.create_or_update_container(Path(folder_path))
            except Exception:  # noqa: BLE001
                logger.exception("Retrying container refresh failed for %s", folder_path)

    def refresh_current_container(self) -> None:
        asset = self._main_window._current_asset
        if not is_container_asset(asset):
            return
        self.create_or_update_container(
            Path(asset.path),
            show_container=True,
        )

    def watch_container_folder(self, folder: Path) -> None:
        # Reset watchers to only current container folder
        try:
            if self._main_window._watched_dirs:
                self._main_window._fs_watcher.removePaths(list(self._main_window._watched_dirs))
        except Exception:
            pass
        if not self._main_window._auto_refresh_containers:
            self._main_window._fs_watcher.addPath(str(folder))
            self._main_window._watched_dirs.add(str(folder))

    def on_fs_changed(self, changed_path: str) -> None:
        if not self._main_window._auto_refresh_containers:
            return
        self._main_window._fs_debounce.start()

    def update_container_watchers(self) -> None:
        if not self._main_window._auto_refresh_containers:
            try:
                if self._main_window._watched_dirs:
                    self._main_window._fs_watcher.removePaths(list(self._main_window._watched_dirs))
            except Exception:
                pass
            self._main_window._watched_dirs = set()
            return

        asset = self._main_window._current_asset
        if not is_container_asset(asset):
            try:
                if self._main_window._watched_dirs:
                    self._main_window._fs_watcher.removePaths(list(self._main_window._watched_dirs))
            except Exception:
                pass
            self._main_window._watched_dirs = set()
            return

        try:
            self.watch_container_folder(Path(asset.path))
        except Exception:
            pass

    def add_container_attachments(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        files, _ = QFileDialog.getOpenFileNames(self._main_window, "Select files to upload")
        if not files:
            return
        self.add_container_attachments_from_files(files)

    def add_container_attachments_from_files(self, files: list[str]) -> None:
        asset = self._main_window._current_asset
        if not is_container_asset(asset):
            self._main_window.statusBar().showMessage(
                "Select a container before adding attachments.",
                3000,
            )
            return
        container_folder = Path(asset.path).expanduser().resolve()
        if not container_folder.is_dir():
            self._main_window.statusBar().showMessage("Container path is not a folder on disk.", 3000)
            return

        try:
            selected = self._main_window._container_pane.selected_item()
        except Exception:
            selected = None

        attachments_dir = _resolve_attachment_directory(container_folder, selected)
        try:
            attachments_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            attachments_dir = container_folder
            attachments_dir.mkdir(parents=True, exist_ok=True)

        def _unique(dest_dir: Path, name: str) -> Path:
            base = Path(name).name
            candidate = dest_dir / base
            if not candidate.exists():
                return candidate
            stem, suffix = Path(base).stem, Path(base).suffix
            i = 1
            while True:
                cand = dest_dir / f"{stem}_{i}{suffix}"
                if not cand.exists():
                    return cand
                i += 1

        added_attachments: list[dict[str, Any]] = []
        created_components: list[Path] = []
        for src in files:
            try:
                source = Path(src).expanduser().resolve(strict=True)
            except OSError:
                continue
            if source.is_dir():
                continue
            dest = _unique(attachments_dir, source.name)
            try:
                shutil.copy2(source, dest)
            except OSError:
                continue
            ctype, _ = mimetypes.guess_type(str(dest))
            rel_path = None
            try:
                rel_path = str(dest.relative_to(container_folder))
            except Exception:
                rel_path = None

            suffix = dest.suffix.casefold()
            if suffix in SUPPORTED_EXTENSIONS:
                created_components.append(dest)
                continue

            entry = {
                "path": str(dest),
                "label": source.name,
                "content_type": ctype or "application/octet-stream",
            }
            if rel_path is not None:
                entry["rel_path"] = rel_path
            entry["metadata"] = build_attachment_metadata(
                dest,
                container_root=container_folder,
                source_path=source,
            )
            added_attachments.append(entry)

        if not added_attachments and not created_components:
            self._main_window.statusBar().showMessage("No files were uploaded.", 3000)
            return

        original_metadata = dict(asset.metadata or {})

        def _undo():
            self._main_window._asset_service.update_asset(asset.id, metadata=original_metadata)
            self._main_window._show_container(asset)

        def _redo():
            meta = dict(asset.metadata or {})
            existing = list(meta.get("attachments") or [])
            meta["attachments"] = existing + added_attachments
            refreshed = self._main_window._asset_service.update_asset(asset.id, metadata=meta)
            self._main_window._show_container(refreshed)

        self._main_window._undo_manager.add(_undo, _redo)

        refreshed = asset
        if added_attachments:
            meta = dict(asset.metadata or {})
            existing = list(meta.get("attachments") or [])
            meta["attachments"] = existing + added_attachments
            refreshed = self._main_window._asset_service.update_asset(asset.id, metadata=meta)
            self._main_window._show_container(refreshed)

        status_bits: list[str] = []
        if added_attachments:
            status_bits.append(f"uploaded {len(added_attachments)} file(s)")
        if created_components:
            status_bits.append(f"imported {len(created_components)} component file(s)")
        if status_bits:
            self._main_window.statusBar().showMessage(
                ", ".join(status_bits).capitalize(),
                4000,
            )

        if created_components:
            focus_component = str(created_components[-1]) if created_components else None
            self.create_or_update_container(
                container_folder,
                show_container=True,
                focus_component=focus_component,
                display_name=asset.metadata.get("display_name") if isinstance(asset.metadata, dict) else None,
            )

    def set_primary_component(self, component_path: str) -> None:
        asset = self._main_window._current_asset
        if asset is None or not isinstance(asset.metadata, dict):
            self._main_window.statusBar().showMessage(
                "Select a container before setting the primary model.",
                3000,
            )
            return
        container_folder = Path(asset.path).expanduser().resolve()
        try:
            candidate = Path(component_path).expanduser().resolve()
        except Exception:
            self._main_window.statusBar().showMessage(
                "Unable to resolve the selected component.",
                3000,
            )
            return

        try:
            candidate.relative_to(container_folder)
        except Exception:
            self._main_window.statusBar().showMessage(
                "Component must live inside the container folder.",
                4000,
            )
            return

        rel_path = _component_relative_path(candidate, container_folder)
        if rel_path is None:
            self._main_window.statusBar().showMessage(
                "Component must live inside the container folder.",
                4000,
            )
            return

        original_metadata = dict(asset.metadata or {})

        def _undo():
            self._main_window._asset_service.update_asset(asset.id, metadata=original_metadata)
            self._main_window._current_asset = self._main_window._asset_service.get_asset(asset.id)
            self.create_or_update_container(
                focus_component=str(candidate),
            )

        def _redo():
            meta = dict(asset.metadata or {})
            raw_map = meta.get("primary_components")
            primary_map = dict(raw_map) if isinstance(raw_map, dict) else {}
            primary_map[rel_path] = rel_path
            meta["primary_components"] = primary_map
            updated = self._main_window._asset_service.update_asset(asset.id, metadata=meta)
            self._main_window._current_asset = updated
            self.create_or_update_container(
                focus_component=str(candidate),
            )

        self._main_window._undo_manager.add(_undo, _redo)

        meta = dict(asset.metadata or {})
        raw_map = meta.get("primary_components")
        primary_map = dict(raw_map) if isinstance(raw_map, dict) else {}
        primary_map[rel_path] = rel_path
        meta["primary_components"] = primary_map
        updated = self._main_window._asset_service.update_asset(asset.id, metadata=meta)
        self._main_window._current_asset = updated

        self._main_window.statusBar().showMessage(
            f"Set '{candidate.name}' as the primary model for this container.",
            4000,
        )
        self.create_or_update_container(
            focus_component=str(candidate),
        )

    def open_current_container_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        asset = self._main_window._current_asset
        if asset is None or not isinstance(asset.metadata, dict):
            self._main_window.statusBar().showMessage("Select a container to open its folder.", 3000)
            return
        kind = str(asset.metadata.get("kind") or "").lower()
        if kind != "container":
            self._main_window.statusBar().showMessage("Select a container to open its folder.", 3000)
            return
        folder = Path(asset.path).expanduser()
        if not folder.exists():
            self._main_window.statusBar().showMessage(
                "Container folder does not exist on disk.",
                3000,
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def new_empty_container_dialog(self) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        root = get_config().library_root
        name, ok = QInputDialog.getText(
            self._main_window,
            "New Container",
            "Container name (folder under library):",
        )
        if not ok:
            return
        name = str(name).strip()
        if not name:
            QMessageBox.warning(
                self._main_window,
                "Invalid name",
                "Container name cannot be empty.",
            )
            return

        # Allocate a unique folder under the library root
        folder = root / str(uuid.uuid4())
        while folder.exists():
            folder = root / str(uuid.uuid4())
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            QMessageBox.critical(
                self._main_window,
                "Error",
                f"Unable to create folder: {folder}",
            )
            return

        # Create container asset with empty components
        label = f"Container: {name}"
        metadata = {
            "kind": "container",
            "container_type": "container",
            "components": [],
            "display_name": name,
            "container_path": str(folder),
            "created_from_ui": True,
        }
        existing = self._main_window._asset_service.get_asset_by_path(str(folder))
        if existing is None:
            self._main_window._asset_service.create_asset(
                str(folder),
                label=label,
                metadata=metadata,
            )
        else:
            self._main_window._asset_service.update_asset(
                existing.id,
                label=label,
                metadata=metadata,
            )

        self.create_or_update_container(
            folder,
            show_container=True,
            select_in_repo=True,
            display_name=name,
            container_type="container",
        )

    def create_new_container(self, *, asset=None) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        from ..storage.container_service import ContainerService

        root = get_config().library_root
        name, ok = QInputDialog.getText(
            self._main_window,
            "New Container",
            "Container name:",
        )
        if not ok:
            return
        name = str(name).strip()
        if not name:
            QMessageBox.warning(
                self._main_window,
                "Invalid name",
                "Container name cannot be empty.",
            )
            return

        container_service = ContainerService(self._main_window._asset_service)
        container_metadata = {
            "display_name": name,
            "created_from_ui": True,
        }

        try:
            created_asset, container_dir = container_service.create_container(
                name=name,
                root=root,
                metadata=container_metadata,
            )
        except Exception as exc:
            QMessageBox.critical(
                self._main_window,
                "Error",
                f"Unable to create container: {exc}",
            )
            return

        if created_asset is not None:
            self._main_window._current_asset = created_asset

        self.create_or_update_container(
            container_dir,
            show_container=True,
            select_in_repo=True,
            display_name=name,
            container_type="container",
        )

    def link_container_into_current_container(self) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        from ..storage.container_service import ContainerService

        container_asset = self._main_window._current_asset
        if not is_container_asset(container_asset):
            QMessageBox.information(
                self._main_window,
                "Link Container",
                "Select a container before linking another container.",
            )
            return

        try:
            container_folder = Path(container_asset.path).expanduser().resolve()
        except Exception:
            QMessageBox.warning(
                self._main_window,
                "Link Container",
                "Container path could not be resolved.",
            )
            return

        if not container_folder.exists() or not container_folder.is_dir():
            QMessageBox.warning(
                self._main_window,
                "Link Container",
                "Container folder is unavailable on disk.",
            )
            return

        candidates: list[tuple[str, str, Any, Path]] = []
        seen_paths: set[str] = set()
        for asset in self._main_window._asset_service.list_assets():
            if not is_container_asset(asset):
                continue
            metadata = asset.metadata or {}
            try:
                asset_folder = Path(asset.path).expanduser().resolve()
            except Exception:
                continue
            if not asset_folder.exists() or not asset_folder.is_dir():
                continue
            if asset_folder == container_folder:
                continue
            try:
                asset_folder.relative_to(container_folder)
            except Exception:
                pass
            else:
                continue
            key = str(asset_folder)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            raw_display = metadata.get("display_name")
            base_label = (
                raw_display.strip()
                if isinstance(raw_display, str) and raw_display.strip()
                else self._friendly_asset_label(asset)
            )
            if not base_label:
                base_label = asset_folder.name
            candidates.append((base_label, base_label, asset, asset_folder))

        if not candidates:
            QMessageBox.information(
                self._main_window,
                "Link Container",
                "No other containers are available to link.",
            )
            return

        candidates.sort(key=lambda item: item[0].casefold())
        options: list[str] = []
        label_counts: dict[str, int] = {}
        decorated: list[tuple[str, str, Any, Path]] = []
        for label, alias_source, asset, folder in candidates:
            count = label_counts.get(label, 0)
            label_counts[label] = count + 1
            if count:
                display_label = f"{label} ({folder.name})"
            else:
                display_label = label
            options.append(display_label)
            decorated.append((display_label, alias_source, asset, folder))

        selection, accepted = QInputDialog.getItem(
            self._main_window,
            "Link Container",
            "Select container to link:",
            options,
            0,
            False,
        )
        if not accepted:
            return

        chosen = next((entry for entry in decorated if entry[0] == selection), None)
        if chosen is None:
            return

        _display_label, alias_source, target_asset, target_folder = chosen
        version_records = self._main_window._asset_service.list_container_versions(target_asset.id)
        selected_version = None
        if version_records:
            version_options = []
            option_labels: list[str] = []
            for record in version_records:
                label = self._format_version_option(record)
                version_options.append((label, record))
                option_labels.append(label)
            working_copy_label = "Working Copy (no version)"
            version_options.append((working_copy_label, None))
            option_labels.append(working_copy_label)
            version_choice, version_ok = QInputDialog.getItem(
                self._main_window,
                "Link Container Version",
                "Select version to link:",
                option_labels,
                0,
                False,
            )
            if not version_ok:
                return
            selected_entry = next(
                (entry for entry in version_options if entry[0] == version_choice),
                None,
            )
            if selected_entry is not None:
                selected_version = selected_entry[1]

        original_source_metadata = dict(container_asset.metadata or {})
        original_target_metadata = dict(target_asset.metadata or {})

        def _undo():
            self._main_window._asset_service.update_asset(container_asset.id, metadata=original_source_metadata)
            self._main_window._asset_service.update_asset(target_asset.id, metadata=original_target_metadata)
            self._main_window._current_asset = self._main_window._asset_service.get_asset(container_asset.id)
            self.create_or_update_container(
                None,
                show_container=True,
                select_in_repo=True,
            )

        def _redo():
            updated_source, updated_target = container_service.link_containers(
                container_asset,
                target_asset,
                link_type="link",
                target_version_id=selected_version.id if selected_version else None,
            )
            if updated_source is not None:
                self._main_window._current_asset = updated_source

            self.create_or_update_container(
                None,
                show_container=True,
                select_in_repo=True,
                focus_component=str(target_folder),
                display_name=(
                    updated_source.metadata.get("display_name") if isinstance(updated_source.metadata, dict) else None
                ),
            )

        self._main_window._undo_manager.add(_undo, _redo)

        container_service = ContainerService(self._main_window._asset_service)
        try:
            updated_source, updated_target = container_service.link_containers(
                container_asset,
                target_asset,
                link_type="link",
                target_version_id=selected_version.id if selected_version else None,
            )
        except ValueError as exc:
            QMessageBox.warning(
                self._main_window,
                "Link Container",
                str(exc) or "Unable to create versioned link.",
            )
            return

        if updated_source is not None:
            self._main_window._current_asset = updated_source

    def _prompt_for_readme(self, container_folder: Path, asset: Any) -> None:
        """Prompt the user to create a README.md if one is missing."""
        readme_path = container_folder / "README.md"
        if readme_path.exists():
            return

        display_name = asset.metadata.get("display_name") if isinstance(asset.metadata, dict) else asset.label
        if not display_name:
            display_name = container_folder.name

        msg = QMessageBox(self._main_window)
        msg.setWindowTitle("Missing README")
        msg.setText(f"The container '{display_name}' does not have a README.md file.")
        msg.setInformativeText("Would you like to add one now?")

        btn_create = msg.addButton("Create from Template", QMessageBox.ButtonRole.AcceptRole)
        btn_upload = msg.addButton("Upload Existing...", QMessageBox.ButtonRole.ActionRole)
        msg.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)

        msg.exec_()

        if msg.clickedButton() == btn_create:
            try:
                content = DEFAULT_README_CONTENT.format(name=display_name)
                readme_path.write_text(content, encoding="utf-8")
                self._main_window.statusBar().showMessage(f"Created README.md for '{display_name}'", 3000)
                # Refresh to show the new file
                self.create_or_update_container(container_folder, show_container=True)
            except Exception as e:
                QMessageBox.critical(self._main_window, "Error", f"Failed to create README.md: {e}")

        elif msg.clickedButton() == btn_upload:
            file_path, _ = QFileDialog.getOpenFileName(
                self._main_window, "Select README File", str(Path.home()), "Markdown Files (*.md);;All Files (*)"
            )
            if file_path:
                try:
                    shutil.copy2(file_path, readme_path)
                    self._main_window.statusBar().showMessage(f"Uploaded README.md for '{display_name}'", 3000)
                    # Refresh to show the new file
                    self.create_or_update_container(container_folder, show_container=True)
                except Exception as e:
                    QMessageBox.critical(self._main_window, "Error", f"Failed to upload README.md: {e}")

    def import_component_from_linked_container(self) -> None:
        from PySide6.QtWidgets import QDialog, QMessageBox

        from ..ui.linked_import_dialog import LinkedContainerOption, LinkedImportDialog

        container_asset = self._main_window._current_asset
        if not is_container_asset(container_asset):
            QMessageBox.information(
                self._main_window,
                "Import From Linked Container",
                "Select a container before importing components.",
            )
            return

        links_meta = container_asset.metadata.get("links") if isinstance(container_asset.metadata, Mapping) else None
        if not isinstance(links_meta, list) or not links_meta:
            QMessageBox.information(
                self._main_window,
                "Import From Linked Container",
                "Link another container first to import its components.",
            )
            return

        options: list[LinkedContainerOption] = []
        container_cache: dict[int, Any] = {}
        seen_targets: set[int] = set()

        for entry in links_meta:
            if not isinstance(entry, Mapping):
                continue
            kind = str(entry.get("kind") or "").strip().casefold()
            if kind and kind != "link":
                continue
            metadata_entry = entry.get("metadata") if hasattr(entry, "get") else None
            if isinstance(metadata_entry, Mapping):
                direction = str(metadata_entry.get("link_direction") or "").strip().casefold()
                if direction and direction != "outgoing":
                    continue
            target_id = entry.get("target_container_id")
            target_asset = None
            if target_id is not None:
                try:
                    target_asset = self._main_window._asset_service.get_asset(int(target_id))
                except (TypeError, ValueError):
                    target_asset = None
            if target_asset is None:
                path_value = str(entry.get("target_path") or entry.get("path") or "").strip()
                if path_value:
                    target_asset = self._main_window._asset_service.get_asset_by_path(path_value)
            if not is_container_asset(target_asset):
                continue
            if target_asset.id in seen_targets:
                continue

            version_label = None
            components_source = None
            version_id = entry.get("target_version_id")
            if version_id is not None:
                try:
                    version_obj = self._main_window._asset_service.get_container_version(int(version_id))
                except (TypeError, ValueError):
                    version_obj = None
                if version_obj is not None and version_obj.container_asset_id == target_asset.id:
                    components_source = version_obj.metadata.get("components")
                    version_label = self._format_version_option(version_obj)

            if components_source is None and isinstance(target_asset.metadata, Mapping):
                components_source = target_asset.metadata.get("components")

            component_entries = self._clone_component_entries(components_source)
            if not component_entries:
                continue

            seen_targets.add(target_asset.id)
            container_cache[target_asset.id] = target_asset
            friendly_label = self._friendly_asset_label(target_asset)
            label = f"{friendly_label} – {version_label}" if version_label else friendly_label
            options.append(
                LinkedContainerOption(
                    container_id=target_asset.id,
                    label=label,
                    path=target_asset.path,
                    components=tuple(component_entries),
                )
            )

        if not options:
            QMessageBox.information(
                self._main_window,
                "Import From Linked Container",
                "No linked containers with components are available.",
            )
            return

        dialog = LinkedImportDialog(self._main_window, options)
        if dialog.exec() != QDialog.Accepted:
            return

        selection = dialog.selection()
        if selection is None:
            return

        source_container_id, component_payload = selection
        source_asset = container_cache.get(source_container_id)
        if source_asset is None:
            source_asset = self._main_window._asset_service.get_asset(source_container_id)
        if not is_container_asset(source_asset):
            QMessageBox.warning(
                self._main_window,
                "Import From Linked Container",
                "Selected container is unavailable.",
            )
            return

        component_entry = dict(component_payload)
        metadata_entry = component_entry.get("metadata")
        if isinstance(metadata_entry, Mapping):
            component_entry["metadata"] = dict(metadata_entry)

        try:
            linked_entry = build_linked_component_entry(component_entry, source_asset)
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(
                self._main_window,
                "Import From Linked Container",
                str(exc) or "Unable to import the selected component.",
            )
            return

        link_meta = linked_entry.get("metadata", {}).get("link_import")
        source_path = ""
        source_label = "the source container"
        source_container_ref = None
        if isinstance(link_meta, Mapping):
            source_path = str(link_meta.get("source_component_path") or "").strip()
            raw_label = link_meta.get("source_container_label")
            if isinstance(raw_label, str) and raw_label.strip():
                source_label = raw_label.strip()
            source_container_ref = link_meta.get("source_container_id")

        original_metadata = dict(container_asset.metadata or {})

        def _undo():
            self._main_window._asset_service.update_asset(container_asset.id, metadata=original_metadata)
            self._main_window._current_asset = self._main_window._asset_service.get_asset(container_asset.id)
            self._main_window._show_container(self._main_window._current_asset)

        def _redo():
            container_metadata = dict(container_asset.metadata) if isinstance(container_asset.metadata, Mapping) else {}
            component_list = list(container_metadata.get("components") or [])
            component_list.append(linked_entry)
            container_metadata["components"] = component_list
            updated_asset = self._main_window._asset_service.update_asset(
                container_asset.id,
                metadata=container_metadata,
            )
            if updated_asset is not None:
                self._main_window._current_asset = updated_asset
            self._main_window._show_container(self._main_window._current_asset)
            focus_target = linked_entry.get("path")
            if isinstance(focus_target, str) and focus_target:
                try:
                    self._main_window._container_pane.focus_matching_item([focus_target])
                except Exception:
                    pass

        self._main_window._undo_manager.add(_undo, _redo)

        container_metadata = dict(container_asset.metadata) if isinstance(container_asset.metadata, Mapping) else {}
        component_list = list(container_metadata.get("components") or [])

        if (
            source_container_ref is not None
            and source_path
            and self._has_existing_linked_component(
                component_list,
                source_container_id=source_container_ref,
                source_path=source_path,
            )
        ):
            QMessageBox.information(
                self._main_window,
                "Import From Linked Container",
                "That component has already been imported.",
            )
            return

        component_list.append(linked_entry)
        container_metadata["components"] = component_list
        updated_asset = self._main_window._asset_service.update_asset(
            container_asset.id,
            metadata=container_metadata,
        )
        if updated_asset is not None:
            container_asset = updated_asset
            self._main_window._current_asset = updated_asset

        self._main_window._show_container(container_asset)
        focus_target = linked_entry.get("path")
        if isinstance(focus_target, str) and focus_target:
            try:
                self._main_window._container_pane.focus_matching_item([focus_target])
            except Exception:
                pass

        self._main_window.statusBar().showMessage(
            f"Imported '{linked_entry.get('label', 'component')}' from {source_label}.",
            4000,
        )

    def _friendly_asset_label(self, asset: Any) -> str:
        if asset is None:
            return ""
        try:
            return self._main_window._library_manager.friendly_asset_label(asset)
        except Exception:
            try:
                return Path(asset.path).name
            except Exception:
                return "Linked Container"

    @staticmethod
    def _clone_component_entries(entries: object) -> list[dict[str, Any]]:
        if not isinstance(entries, list):
            return []
        cloned: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            path_value = entry.get("path")
            try:
                path_text = str(path_value or "").strip()
            except Exception:
                continue
            if not path_text:
                continue
            payload = dict(entry)
            metadata_entry = payload.get("metadata")
            if isinstance(metadata_entry, Mapping):
                payload["metadata"] = dict(metadata_entry)
            cloned.append(payload)
        return cloned

    @staticmethod
    def _has_existing_linked_component(
        entries: list[Any],
        *,
        source_container_id: int,
        source_path: str,
    ) -> bool:
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            if entry.get("kind") != "linked_component":
                continue
            metadata_entry = entry.get("metadata")
            if not isinstance(metadata_entry, Mapping):
                continue
            link_meta = metadata_entry.get("link_import")
            if not isinstance(link_meta, Mapping):
                continue
            if link_meta.get("source_container_id") != source_container_id:
                continue
            candidate_path = str(link_meta.get("source_component_path") or "").strip()
            if not candidate_path:
                continue
            if candidate_path == source_path:
                return True
        return False

    @staticmethod
    def _format_version_option(
        version: ContainerVersionRecord,
    ) -> str:
        timestamp = version.created_at
        try:
            localized = timestamp.astimezone()
        except Exception:
            localized = timestamp
        return f"{version.name} ({localized.strftime('%Y-%m-%d %H:%M')})"


def _component_relative_path(component_path: Path, container_folder: Path) -> str | None:
    try:
        relative = component_path.expanduser().resolve().relative_to(container_folder)
    except Exception:
        return None
    text = relative.as_posix()
    return "." if not text or text == "." else text


def _resolve_attachment_directory(container_folder: Path, selected_item: QListWidgetItem | None) -> Path:
    if selected_item is not None:
        try:
            from PySide6.QtCore import Qt

            raw_path = selected_item.data(Qt.UserRole + 2)
        except Exception:
            raw_path = None

        if raw_path:
            try:
                path = Path(str(raw_path)).expanduser().resolve()
                if path.is_dir():
                    return path
                return path.parent
            except Exception:
                pass
    return container_folder
