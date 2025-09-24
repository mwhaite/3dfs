"""Application bootstrap for the 3dfs desktop shell."""

from __future__ import annotations

import mimetypes
import os
import shutil
import sys
from pathlib import Path
from typing import Final

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenuBar,
    QSplitter,
    QWidget,
)

from .assembly import discover_arrangement_scripts
from .config import get_config
from .data import TagStore
from .importer import SUPPORTED_EXTENSIONS
from .customizer.pipeline import PipelineResult
from .storage import AssetService
from .ui import AssemblyPane, PreviewPane, TagSidebar

WINDOW_TITLE: Final[str] = "3dfs"


class MainWindow(QMainWindow):
    """Primary window for the 3dfs shell."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)

        self._asset_service = AssetService()
        self._tag_store = TagStore(service=self._asset_service)
        self._tag_sidebar = TagSidebar(
            self._tag_store, asset_service=self._asset_service
        )
        self._repository_list = QListWidget(self)
        self._repository_list.setObjectName("repositoryList")
        self._repository_list.setSelectionMode(QAbstractItemView.SingleSelection)
        # Right-click context menu on repository list
        self._repository_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._repository_list.customContextMenuRequested.connect(
            self._show_repo_context_menu
        )

        config = get_config()
        self._preview_pane = PreviewPane(
            base_path=config.library_root,
            asset_service=self._asset_service,
            parent=self,
        )
        self._preview_pane.setObjectName("previewPane")
        self._preview_pane.navigationRequested.connect(
            self._handle_preview_navigation
        )
        self._preview_pane.customizationGenerated.connect(
            self._handle_customization_generated
        )

        # Assembly pane shares the right split area via a stacked layout
        self._assembly_pane = AssemblyPane(self)
        self._assembly_pane.setObjectName("assemblyPane")
        # Wire assembly pane actions
        self._assembly_pane.newPartRequested.connect(self._create_new_part)
        self._assembly_pane.addAttachmentsRequested.connect(
            self._add_assembly_attachments
        )
        self._assembly_pane.openFolderRequested.connect(
            self._open_current_assembly_folder
        )
        self._assembly_pane.openItemFolderRequested.connect(self._open_item_folder)
        self._assembly_pane.navigateUpRequested.connect(self._navigate_up_assembly)
        self._assembly_pane.navigateToPathRequested.connect(self._navigate_to_path)
        self._assembly_pane.filesDropped.connect(
            self._add_assembly_attachments_from_files
        )
        self._assembly_pane.refreshRequested.connect(self._refresh_current_assembly)

        # File system watcher for live assembly refresh
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(self._on_fs_changed)
        self._fs_watcher.fileChanged.connect(self._on_fs_changed)
        self._fs_debounce = QTimer(self)
        self._fs_debounce.setSingleShot(True)
        self._fs_debounce.setInterval(400)
        self._fs_debounce.timeout.connect(self._refresh_current_assembly)
        self._watched_dirs: set[str] = set()

        self._build_layout()
        self._connect_signals()
        self._build_menu()
        self._populate_repository()
        self._current_asset = None
        # If nothing is persisted yet, attempt an initial rescan to discover
        # assets already present in the configured library directory.
        if self._repository_list.count() == 0:
            self._rescan_library()
        # Hide repository sidebar by default; assembly view takes the space
        self._toggle_repository_sidebar(False)

    # ------------------------------------------------------------------
    # Layout & wiring
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        from PySide6.QtWidgets import (
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QVBoxLayout,
        )

        central_widget = QWidget(self)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Repository sidebar container (search + list)
        self._repo_container = QWidget(central_widget)
        repo_layout = QVBoxLayout(self._repo_container)
        repo_layout.setContentsMargins(0, 0, 0, 0)
        repo_layout.setSpacing(0)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(6, 6, 6, 6)
        search_row.setSpacing(6)
        search_label = QLabel("Search:")
        self._repo_search_input = QLineEdit()
        self._repo_search_input.setPlaceholderText("Filter by name or path…")
        self._repo_search_input.textChanged.connect(self._apply_repository_filters)
        search_row.addWidget(search_label)
        search_row.addWidget(self._repo_search_input, 1)
        repo_layout.addLayout(search_row)
        repo_layout.addWidget(self._repository_list, 1)

        splitter = QSplitter(Qt.Horizontal, central_widget)
        splitter.addWidget(self._repo_container)

        # Stack preview and assembly panes
        from PySide6.QtWidgets import QStackedWidget

        self._detail_stack = QStackedWidget(central_widget)
        self._detail_stack.addWidget(self._preview_pane)  # index 0
        self._detail_stack.addWidget(self._assembly_pane)  # index 1

        splitter.addWidget(self._detail_stack)
        splitter.addWidget(self._tag_sidebar)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 1)

        layout.addWidget(splitter)
        self.setCentralWidget(central_widget)

    def _connect_signals(self) -> None:
        self._repository_list.currentItemChanged.connect(self._handle_selection_change)
        self._tag_sidebar.searchRequested.connect(self._handle_search_request)
        self._tag_sidebar.tagsChanged.connect(self._handle_tags_changed)
        self._tag_sidebar.derivativeActivated.connect(self._handle_preview_navigation)

    def _populate_repository(self) -> None:
        """Populate the repository view with persisted asset entries."""

        self._repository_list.clear()
        # By default show only persisted assets; opt-in demo seeding via env var.
        if os.environ.get("THREE_DFS_BOOTSTRAP_DEMO"):
            assets = self._asset_service.bootstrap_demo_data()
        else:
            assets = self._asset_service.list_assets()

        for asset in assets:
            display_label = asset.label or asset.path
            item = QListWidgetItem(display_label)
            item.setData(Qt.UserRole, asset.path)
            item.setToolTip(asset.path)
            self._repository_list.addItem(item)

        if self._repository_list.count():
            self._repository_list.setCurrentRow(0)
        else:
            # Surface the current library root to help users locate files.
            self.statusBar().showMessage(f"Library: {get_config().library_root}", 5000)

    # ------------------------------------------------------------------
    # Menu & actions
    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        menubar = self.menuBar()
        if menubar is None:
            menubar = QMenuBar(self)
            self.setMenuBar(menubar)

        file_menu = menubar.addMenu("&File")

        rescan_action = QAction("Rescan Library", self)
        rescan_action.setShortcut("F5")
        rescan_action.triggered.connect(self._rescan_library)
        file_menu.addAction(rescan_action)

        # Assembly actions
        assembly_menu = menubar.addMenu("&Assemblies")
        from PySide6.QtWidgets import QFileDialog

        def _new_assembly_from_folder() -> None:
            config = get_config()
            folder = QFileDialog.getExistingDirectory(
                self,
                "Choose project folder",
                str(config.library_root),
            )
            if not folder:
                return
            self._create_or_update_assembly(Path(folder))

        new_assembly_action = QAction("New Assembly From Folder…", self)
        new_assembly_action.triggered.connect(_new_assembly_from_folder)
        assembly_menu.addAction(new_assembly_action)

        def _new_empty_assembly() -> None:
            from PySide6.QtWidgets import QInputDialog, QMessageBox

            root = get_config().library_root
            name, ok = QInputDialog.getText(
                self,
                "New Assembly",
                "Assembly name (folder under library):",
            )
            if not ok:
                return
            name = str(name).strip()
            if not name:
                QMessageBox.warning(
                    self,
                    "Invalid name",
                    "Assembly name cannot be empty.",
                )
                return

            # Allocate a unique folder under the library root
            base = root / name
            folder = base
            counter = 1
            while folder.exists() and not folder.is_dir():
                folder = root / f"{name}_{counter}"
                counter += 1
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except Exception:
                QMessageBox.critical(
                    self,
                    "Error",
                    f"Unable to create folder: {folder}",
                )
                return

            # Create assembly asset with empty components
            label = f"Assembly: {folder.name}"
            metadata = {
                "kind": "assembly",
                "components": [],
                "project": folder.name,
            }
            existing = self._asset_service.get_asset_by_path(str(folder))
            if existing is None:
                created = self._asset_service.create_asset(
                    str(folder),
                    label=label,
                    metadata=metadata,
                )
            else:
                created = self._asset_service.update_asset(
                    existing.id,
                    label=label,
                    metadata=metadata,
                )

            # Refresh and show the new assembly
            self._populate_repository()
            self._show_assembly(created)
            # Optionally select in repository sidebar if visible
            try:
                if (
                    getattr(self, "_repo_container", None) is not None
                    and self._repo_container.isVisible()
                ):
                    for row in range(self._repository_list.count()):
                        it = self._repository_list.item(row)
                        if str(it.data(Qt.UserRole) or it.text()) == str(folder):
                            self._repository_list.setCurrentItem(it)
                            break
            except Exception:
                pass

        new_empty_assembly_action = QAction("New Empty Assembly…", self)
        new_empty_assembly_action.triggered.connect(self._new_empty_assembly_dialog)
        assembly_menu.addAction(new_empty_assembly_action)

        new_part_action = QAction("New Part in Current Assembly…", self)
        new_part_action.triggered.connect(self._create_new_part)
        assembly_menu.addAction(new_part_action)

        add_attachment_action = QAction("Add Attachment(s) to Current Assembly…", self)
        add_attachment_action.triggered.connect(self._add_assembly_attachments)
        assembly_menu.addAction(add_attachment_action)

        organize_parts_action = QAction("Organize Parts Into Folders", self)
        organize_parts_action.triggered.connect(self._organize_parts_into_folders)
        assembly_menu.addAction(organize_parts_action)

        organize_action = QAction("Organize Library", self)
        organize_action.setShortcut("Ctrl+O")
        organize_action.triggered.connect(self._organize_library)
        file_menu.addAction(organize_action)
        # View menu: toggle repository sidebar visibility
        view_menu = menubar.addMenu("&View")
        self._toggle_repo_action = QAction("Show Repository Sidebar", self)
        self._toggle_repo_action.setCheckable(True)
        self._toggle_repo_action.setChecked(False)
        self._toggle_repo_action.toggled.connect(self._toggle_repository_sidebar)
        view_menu.addAction(self._toggle_repo_action)

        # Convenience: open a folder directly as an assembly
        def _open_as_assembly_from_menu() -> None:
            config = get_config()
            folder = QFileDialog.getExistingDirectory(
                self,
                "Open folder as Assembly",
                str(config.library_root),
            )
            if not folder:
                return
            self._create_or_update_assembly(Path(folder))
            # Select in repository list
            for row in range(self._repository_list.count()):
                item = self._repository_list.item(row)
                if str(item.data(Qt.UserRole) or item.text()) == str(folder):
                    self._repository_list.setCurrentItem(item)
                    break

        open_as_assembly_action = QAction("Open Folder as Assembly…", self)
        open_as_assembly_action.triggered.connect(_open_as_assembly_from_menu)
        file_menu.addAction(open_as_assembly_action)

    # ------------------------------------------------------------------
    # Library maintenance
    # ------------------------------------------------------------------
    def _rescan_library(self) -> None:
        """Scan the library root, add new files, prune missing entries."""

        config = get_config()
        root = config.library_root

        added = 0
        removed = 0

        # Index existing assets for quick lookup
        existing_assets = {
            asset.path: asset for asset in self._asset_service.list_assets()
        }

        # Discover files in library root with supported 3D extensions
        discovered: set[str] = set()
        try:
            if root.exists():
                for path in root.rglob("*"):
                    if not path.is_file():
                        continue
                    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                        continue
                    as_str = str(path)
                    discovered.add(as_str)
                    if as_str not in existing_assets:
                        self._asset_service.ensure_asset(as_str, label=path.name)
                        added += 1
        except Exception:
            # Non-fatal; still attempt to prune and refresh
            self.statusBar().showMessage("Error while scanning library", 3000)

        # Prune assets that no longer exist on disk. For relative paths, resolve
        # them against the configured library root to mirror preview behavior.
        for raw_path, asset in list(existing_assets.items()):
            try:
                candidate = Path(raw_path)
                if not candidate.is_absolute():
                    candidate = (root / candidate).resolve()
                if not candidate.exists():
                    if self._asset_service.delete_asset(asset.id):
                        removed += 1
            except Exception:
                # Continue pruning remaining entries
                continue

        self._populate_repository()
        self.statusBar().showMessage(
            f"Rescan complete: +{added} added, -{removed} removed", 4000
        )

    def _organize_library(self) -> None:
        """Group lone model files into per-project folders and update records."""

        config = get_config()
        root = config.library_root

        moved = 0
        errors = 0

        def derive_project_name(path: Path) -> str:
            stem = path.stem
            for sep in ("_", "-"):
                if sep in stem:
                    base = stem.split(sep, 1)[0].strip()
                    if base:
                        return base
            return stem

        for asset in list(self._asset_service.list_assets()):
            try:
                source = Path(asset.path)
                # Only organize assets that live under the configured root
                # and are directly in the root (not already in a subdirectory).
                try:
                    relative = source.resolve().relative_to(root)
                except Exception:
                    continue

                if not source.exists():
                    continue
                if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                if relative.parent != Path("."):
                    continue  # already in a folder

                project_name = derive_project_name(source)
                project_dir = root / project_name
                project_dir.mkdir(parents=True, exist_ok=True)

                destination = project_dir / source.name
                if destination.exists():
                    # Allocate a non-colliding destination
                    counter = 1
                    while True:
                        candidate_name = f"{source.stem}_{counter}{source.suffix}"
                        candidate = project_dir / candidate_name
                        if not candidate.exists():
                            destination = candidate
                            break
                        counter += 1

                source = source.resolve()
                destination = destination.resolve()
                source.rename(destination)

                # Update record path and managed_path in metadata when present
                metadata = dict(asset.metadata or {})
                metadata["project"] = project_name
                managed_path = metadata.get("managed_path")
                if managed_path:
                    try:
                        managed_resolved = (
                            Path(str(managed_path)).expanduser().resolve()
                        )
                    except Exception:
                        managed_resolved = None
                    if managed_resolved is None or managed_resolved == source:
                        metadata["managed_path"] = str(destination)
                else:
                    metadata["managed_path"] = str(destination)

                self._asset_service.update_asset(
                    asset.id,
                    path=str(destination),
                    metadata=metadata,
                )
                moved += 1
            except Exception:
                errors += 1
                continue

        self._populate_repository()
        msg = f"Organize complete: {moved} moved"
        if errors:
            msg += f", {errors} failed"
        self.statusBar().showMessage(msg, 5000)

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------
    def _handle_selection_change(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        del previous  # unused but part of the Qt signal signature

        if current is None:
            self._preview_pane.clear()
            self._tag_sidebar.set_active_item(None)
            return

        item_id = current.data(Qt.UserRole) or current.text()
        item_id = str(item_id)
        asset = self._asset_service.get_asset_by_path(item_id)

        # Assembly detection: assets with kind == 'assembly' in metadata
        if (
            asset is not None
            and isinstance(asset.metadata, dict)
            and (str(asset.metadata.get("kind") or "").lower() == "assembly")
        ):
            self._show_assembly(asset)
            return

        # Unify: if selection is a file, show its folder as assembly context
        target_path = asset.path if asset is not None else item_id
        try:
            target = Path(str(target_path)).expanduser()
        except Exception:
            target = None

        if target is not None and target.exists() and target.is_file():
            parent = target.parent
            try:
                parent.relative_to(get_config().library_root)
            except Exception:
                # Outside library root → preview only
                self._preview_pane.set_item(
                    str(target), label=current.text(), metadata=None, asset_record=asset
                )
                self._detail_stack.setCurrentWidget(self._preview_pane)
                self._tag_sidebar.set_active_item(str(target))
                self._current_asset = asset
                return

            # Load/show assembly for parent and select this file inside
            self._create_or_update_assembly(parent)
            for row in range(self._repository_list.count()):
                it = self._repository_list.item(row)
                if str(it.data(Qt.UserRole) or it.text()) == str(parent):
                    self._repository_list.setCurrentItem(it)
                    break
            parent_asset = self._asset_service.get_asset_by_path(str(parent))
            if parent_asset is not None:
                self._show_assembly(parent_asset)
                try:
                    self._assembly_pane.select_item(str(target))
                except Exception:
                    pass
                self._current_asset = parent_asset
                return

        # Default: preview pane
        if asset is None:
            self._preview_pane.set_item(
                item_id,
                label=current.text(),
                metadata=None,
                asset_record=None,
            )
            self._current_asset = None
        else:
            self._preview_pane.set_item(
                asset.path,
                label=asset.label,
                metadata=asset.metadata,
                asset_record=asset,
            )
            self._current_asset = asset
        # Start watching this assembly folder for changes
        try:
            self._watch_assembly_folder(Path(asset.path))
        except Exception:
            pass

        self._detail_stack.setCurrentWidget(self._preview_pane)
        self._tag_sidebar.set_active_item(item_id)

    def _show_assembly(self, asset) -> None:
        # Build component list from metadata["components"] entries
        meta = dict(asset.metadata or {})
        comps_raw = meta.get("components") or []
        components = []
        kinds = []
        for entry in comps_raw:
            try:
                path = str(entry.get("path") or "").strip()
                label = str(entry.get("label") or Path(path).name)
                kind = str(entry.get("kind") or "component")
            except Exception:
                continue
            if path:
                components.append((path, label))
                kinds.append(kind)

        from .ui.assembly_pane import AssemblyArrangement, AssemblyComponent

        comp_objs = []
        for (path, label), kind in zip(components, kinds, strict=False):
            resolved_kind = (
                kind if kind in {"component", "placeholder"} else "component"
            )
            comp_objs.append(
                AssemblyComponent(path=path, label=label, kind=resolved_kind)
            )
        arr_objs = []
        for entry in meta.get("arrangements") or []:
            if not isinstance(entry, dict):
                continue
            raw_path = entry.get("path")
            path = str(raw_path or "").strip()
            if not path:
                continue
            raw_label = entry.get("label")
            label = str(raw_label).strip() if raw_label is not None else ""
            if not label:
                label = Path(path).stem
            raw_description = entry.get("description")
            description = (
                str(raw_description).strip() if raw_description is not None else None
            )
            if description == "":
                description = None
            rel_path = entry.get("rel_path")
            rel_str = str(rel_path).strip() if isinstance(rel_path, str) else None
            metadata_entry = entry.get("metadata")
            metadata_dict = metadata_entry if isinstance(metadata_entry, dict) else None
            arr_objs.append(
                AssemblyArrangement(
                    path=path,
                    label=label,
                    description=description,
                    rel_path=rel_str or None,
                    metadata=metadata_dict,
                )
            )
        atts_raw = meta.get("attachments") or []
        att_objs = []
        for a in atts_raw:
            if not isinstance(a, dict):
                continue
            path_value = str(a.get("path") or "").strip()
            if not path_value:
                continue
            label_text = str(a.get("label") or Path(path_value).name)
            att_objs.append(
                AssemblyComponent(path=path_value, label=label_text, kind="attachment")
            )

        self._assembly_pane.set_assembly(
            asset.path,
            label=asset.label,
            components=comp_objs,
            arrangements=arr_objs,
            attachments=att_objs,
        )
        self._detail_stack.setCurrentWidget(self._assembly_pane)
        self._tag_sidebar.set_active_item(asset.path)
        self._current_asset = asset
        # Start watching this assembly folder for changes
        try:
            self._watch_assembly_folder(Path(asset.path))
        except Exception:
            pass

    def _handle_search_request(self, query: str) -> None:
        normalized = query.strip()
        if normalized:
            matches = self._tag_store.search(normalized)
            self._tag_filter_matches = set(matches.keys())
            self.statusBar().showMessage(f"Filtering by tag: {normalized}", 2000)
        else:
            self._tag_filter_matches = None
            self.statusBar().showMessage("Cleared tag filter", 2000)
        self._apply_repository_filters()

    def _handle_tags_changed(self, item_id: str, tags: list[str]) -> None:
        current_tag_query = self._tag_sidebar.search_text()
        if current_tag_query:
            matches = self._tag_store.search(current_tag_query)
            self._tag_filter_matches = set(matches.keys())
        else:
            self._tag_filter_matches = None
        self._apply_repository_filters()
        self.statusBar().showMessage(f"{len(tags)} tag(s) assigned to {item_id}", 2000)

    def _handle_preview_navigation(self, target_path: str) -> None:
        if not target_path:
            return
        normalized = str(target_path)
        if self._select_repository_path(normalized):
            return

        asset = self._asset_service.get_asset_by_path(normalized)
        if asset is None:
            self.statusBar().showMessage(f"Asset not found: {normalized}", 4000)
            return

        self._preview_pane.set_item(
            asset.path,
            label=asset.label,
            metadata=asset.metadata,
            asset_record=asset,
        )
        self._detail_stack.setCurrentWidget(self._preview_pane)
        self._tag_sidebar.set_active_item(asset.path)
        self._current_asset = asset

    def _handle_customization_generated(self, payload: object) -> None:
        result = payload if isinstance(payload, PipelineResult) else None
        if result is None:
            return

        current_item = self._repository_list.currentItem()
        current_path = (
            str(current_item.data(Qt.UserRole) or current_item.text())
            if current_item is not None
            else None
        )

        self._populate_repository()
        self._apply_repository_filters()
        if current_path:
            self._select_repository_path(current_path)

        artifact_count = len(result.artifacts)
        noun = "artifact" if artifact_count == 1 else "artifacts"
        self.statusBar().showMessage(
            f"Generated {artifact_count} customized {noun}.", 5000
        )

        active_item = self._tag_sidebar.active_item()
        if active_item:
            self._tag_sidebar.set_active_item(active_item)

    def _apply_repository_filters(self) -> None:
        raw_text = (
            self._repo_search_input.text()
            if hasattr(self, "_repo_search_input")
            else ""
        )
        text_needle = raw_text.strip().casefold()
        tag_matches = getattr(self, "_tag_filter_matches", None)

        for row in range(self._repository_list.count()):
            item = self._repository_list.item(row)
            path = str(item.data(Qt.UserRole) or item.text())
            label = item.text()
            matches_tag = True if tag_matches is None else (path in tag_matches)
            if not text_needle:
                matches_text = True
            else:
                label_case = (label or "").casefold()
                matches_text = (
                    text_needle in label_case or text_needle in path.casefold()
                )
            item.setHidden(not (matches_tag and matches_text))

    # ------------------------------------------------------------------
    # Assembly helpers
    # ------------------------------------------------------------------
    def _create_or_update_assembly(self, folder: Path) -> None:
        folder = folder.expanduser().resolve()
        config = get_config()
        root = config.library_root
        try:
            folder.relative_to(root)
        except Exception:
            self.statusBar().showMessage("Folder must be under the library root", 4000)
            return

        name = folder.name
        label = f"Assembly: {name}"

        # Discover components within folder
        from .importer import SUPPORTED_EXTENSIONS

        components: list[dict] = []
        parts_with_models: set[str] = set()
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            record = self._asset_service.ensure_asset(str(path), label=path.name)
            try:
                parent_dir = str(Path(record.path).parent)
            except Exception:
                parent_dir = str(folder)
            parts_with_models.add(parent_dir)
            # Label by parent folder when nested, else by file stem
            try:
                parent = Path(record.path).parent
                comp_label = parent.name if parent != folder else Path(record.path).stem
            except Exception:
                comp_label = record.label
            components.append(
                {
                    "path": record.path,
                    "label": comp_label,
                    "kind": "component",
                }
            )

        # Include placeholder items for immediate subfolders without a model yet
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
                    }
                )
        except Exception:
            pass

        # Create or update an asset record representing the assembly (path = folder)
        existing = self._asset_service.get_asset_by_path(str(folder))
        preserved_attachments: list[dict] = []
        preserved_arrangements: list[dict] = []
        if existing is not None:
            try:
                preserved_attachments = list(
                    (existing.metadata or {}).get("attachments") or []
                )
            except Exception:
                preserved_attachments = []
            try:
                preserved_arrangements = list(
                    (existing.metadata or {}).get("arrangements") or []
                )
            except Exception:
                preserved_arrangements = []
        try:
            arrangements = discover_arrangement_scripts(folder, preserved_arrangements)
        except Exception:
            arrangements = [dict(entry) for entry in preserved_arrangements]
        metadata = {
            "kind": "assembly",
            "components": components,
            "project": name,
        }
        if preserved_attachments:
            metadata["attachments"] = preserved_attachments
        if arrangements:
            metadata["arrangements"] = arrangements
        if existing is None:
            self._asset_service.create_asset(
                str(folder),
                label=label,
                metadata=metadata,
            )
        else:
            # Merge components while preserving attachments
            self._asset_service.update_asset(
                existing.id,
                metadata=metadata,
                label=label,
            )

        self._populate_repository()
        self.statusBar().showMessage(
            f"Assembly '{name}' updated with {len(components)} component(s)",
            4000,
        )
        # Update watcher to monitor latest folder
        try:
            self._watch_assembly_folder(folder)
        except Exception:
            pass

    def _add_assembly_attachments(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        files, _ = QFileDialog.getOpenFileNames(self, "Select attachment files")
        if not files:
            return
        self._add_assembly_attachments_from_files(files)

    def _add_assembly_attachments_from_files(self, files: list[str]) -> None:
        asset = self._current_asset
        if asset is None or not isinstance(asset.metadata, dict):
            self.statusBar().showMessage("Select an assembly to add attachments.", 3000)
            return
        kind = str(asset.metadata.get("kind") or "").lower()
        if kind != "assembly":
            self.statusBar().showMessage("Select an assembly to add attachments.", 3000)
            return

        assembly_folder = Path(asset.path).expanduser().resolve()
        if not assembly_folder.is_dir():
            self.statusBar().showMessage("Assembly path is not a folder on disk.", 3000)
            return

        # Target: if a component is selected, attach into that component's folder;
        # otherwise, attach into the assembly folder root.
        attachments_dir = assembly_folder
        try:
            selected = self._assembly_pane.selected_item()
        except Exception:
            selected = None
        if selected is not None:
            sel_path, sel_kind = selected
            if sel_kind == "component" and sel_path:
                parent_dir = Path(sel_path).expanduser().parent
                if parent_dir.exists():
                    attachments_dir = parent_dir
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

        added: list[dict] = []
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
            entry = {
                "path": str(dest),
                "label": source.name,
                "content_type": ctype or "application/octet-stream",
            }
            try:
                entry["rel_path"] = str(dest.relative_to(assembly_folder))
            except Exception:
                pass
            added.append(entry)

        if not added:
            self.statusBar().showMessage("No attachments were added.", 3000)
            return

        meta = dict(asset.metadata or {})
        existing = list(meta.get("attachments") or [])
        meta["attachments"] = existing + added
        refreshed = self._asset_service.update_asset(asset.id, metadata=meta)
        self._show_assembly(refreshed)
        self.statusBar().showMessage(f"Added {len(added)} attachment(s)", 4000)

    def _organize_parts_into_folders(self) -> None:
        asset = self._current_asset
        if asset is None or not isinstance(asset.metadata, dict):
            self.statusBar().showMessage("Select an assembly to organize parts.", 3000)
            return
        kind = str(asset.metadata.get("kind") or "").lower()
        if kind != "assembly":
            self.statusBar().showMessage("Select an assembly to organize parts.", 3000)
            return
        folder = Path(asset.path).expanduser().resolve()
        if not folder.is_dir():
            self.statusBar().showMessage("Assembly path is not a folder on disk.", 3000)
            return
        from .importer import SUPPORTED_EXTENSIONS

        moved = 0
        for path in list(folder.glob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            part_dir = folder / path.stem
            part_dir.mkdir(parents=True, exist_ok=True)
            dest = part_dir / path.name
            if dest.exists():
                continue
            try:
                path.rename(dest)
            except OSError:
                continue
            rec = self._asset_service.get_asset_by_path(str(path))
            if rec is not None:
                meta = dict(rec.metadata or {})
                if meta.get("managed_path") in (None, str(path)):
                    meta["managed_path"] = str(dest)
                self._asset_service.update_asset(rec.id, path=str(dest), metadata=meta)
            moved += 1
        self._create_or_update_assembly(folder)
        self.statusBar().showMessage(f"Organized parts: {moved} moved", 4000)

    def _open_current_assembly_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        asset = self._current_asset
        if asset is None or not isinstance(asset.metadata, dict):
            self.statusBar().showMessage("Select an assembly to open its folder.", 3000)
            return
        kind = str(asset.metadata.get("kind") or "").lower()
        if kind != "assembly":
            self.statusBar().showMessage("Select an assembly to open its folder.", 3000)
            return
        folder = Path(asset.path).expanduser()
        if not folder.exists():
            self.statusBar().showMessage(
                "Assembly folder does not exist on disk.",
                3000,
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ------------------------------------------------------------------
    # Assembly refresh helpers
    # ------------------------------------------------------------------
    def _refresh_current_assembly(self) -> None:
        asset = self._current_asset
        if asset is None:
            return
        if not isinstance(asset.metadata, dict):
            return
        kind = str(asset.metadata.get("kind") or "").lower()
        if kind != "assembly":
            return
        folder = Path(asset.path)
        self._create_or_update_assembly(folder)
        latest = self._asset_service.get_asset_by_path(str(folder))
        if latest is not None:
            self._show_assembly(latest)

    def _watch_assembly_folder(self, folder: Path) -> None:
        # Reset watchers to only current assembly folder
        try:
            if self._watched_dirs:
                self._fs_watcher.removePaths(list(self._watched_dirs))
        except Exception:
            pass
        self._watched_dirs = set()
        if folder.exists():
            self._fs_watcher.addPath(str(folder))
            self._watched_dirs.add(str(folder))

    def _on_fs_changed(self, changed_path: str) -> None:
        # Debounce refresh bursts
        self._fs_debounce.start()

    def _open_item_folder(self, item_path: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        folder = Path(item_path).expanduser().parent
        if folder.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _navigate_up_assembly(self) -> None:
        asset = self._current_asset
        if asset is None or not isinstance(asset.metadata, dict):
            self.statusBar().showMessage("Select an assembly to navigate.", 3000)
            return
        kind = str(asset.metadata.get("kind") or "").lower()
        if kind != "assembly":
            self.statusBar().showMessage("Select an assembly to navigate.", 3000)
            return
        current = Path(asset.path).expanduser().resolve()
        parent = current.parent
        try:
            parent.relative_to(get_config().library_root)
        except Exception:
            self.statusBar().showMessage("Reached outside the library root.", 3000)
            return
        # Create/update assembly for the parent folder and show it
        self._create_or_update_assembly(parent)
        # Select the newly created/updated parent assembly in the repo list
        target = str(parent)
        for row in range(self._repository_list.count()):
            item = self._repository_list.item(row)
            if str(item.data(Qt.UserRole) or item.text()) == target:
                self._repository_list.setCurrentItem(item)
                break

    # ------------------------------------------------------------------
    # Repository list context menu
    # ------------------------------------------------------------------
    def _show_repo_context_menu(self, pos) -> None:
        try:
            item = self._repository_list.itemAt(pos)
        except Exception:
            item = None
        if item is not None:
            try:
                self._repository_list.setCurrentItem(item)
            except Exception:
                pass
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        customize_act = None
        if self._preview_pane.can_customize:
            customize_act = menu.addAction("Customize…")
            menu.addSeparator()
        new_assembly_act = menu.addAction("New Empty Assembly…")
        open_assembly_act = menu.addAction("Open as Assembly")
        open_folder_act = menu.addAction("Open Containing Folder")
        global_pos = self._repository_list.mapToGlobal(pos)
        action = menu.exec(global_pos)
        if action is None:
            return
        if action == customize_act:
            self._preview_pane.launch_customizer()
            return
        if action == new_assembly_act:
            self._new_empty_assembly_dialog()
            return
        if action == open_assembly_act:
            # Use item path if folder; otherwise parent folder
            target = None
            if item is not None:
                target = str(item.data(Qt.UserRole) or item.text())
            if target:
                p = Path(target).expanduser()
                folder = p if p.is_dir() else p.parent
                try:
                    folder = folder.resolve()
                except Exception:
                    return
                try:
                    folder.relative_to(get_config().library_root)
                except Exception:
                    self.statusBar().showMessage(
                        "Folder is outside the library root.",
                        3000,
                    )
                    return
                self._create_or_update_assembly(folder)
                # Select assembly
                for row in range(self._repository_list.count()):
                    candidate = self._repository_list.item(row)
                    candidate_path = str(
                        candidate.data(Qt.UserRole) or candidate.text()
                    )
                    if candidate_path == str(folder):
                        self._repository_list.setCurrentItem(candidate)
                        break
        elif action == open_folder_act:
            target = None
            if item is not None:
                target = str(item.data(Qt.UserRole) or item.text())
            if target:
                self._open_item_folder(target)

    def _navigate_to_path(self, target_path: str) -> None:
        try:
            folder = Path(target_path).expanduser().resolve()
        except Exception:
            return
        try:
            folder.relative_to(get_config().library_root)
        except Exception:
            return
        self._create_or_update_assembly(folder)
        for row in range(self._repository_list.count()):
            item = self._repository_list.item(row)
            item_path = str(item.data(Qt.UserRole) or item.text())
            if item_path == str(folder):
                self._repository_list.setCurrentItem(item)
                break

    def _toggle_repository_sidebar(self, show: bool) -> None:
        # Toggle repo container visibility and adjust splitter automatically
        if getattr(self, "_repo_container", None) is not None:
            self._repo_container.setVisible(bool(show))

    def _select_repository_path(self, target_path: str) -> bool:
        normalized = str(target_path)
        for row in range(self._repository_list.count()):
            item = self._repository_list.item(row)
            item_path = str(item.data(Qt.UserRole) or item.text())
            if item_path == normalized:
                self._repository_list.setCurrentItem(item)
                return True
        return False

    def _new_empty_assembly_dialog(self) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        root = get_config().library_root
        name, ok = QInputDialog.getText(
            self,
            "New Assembly",
            "Assembly name (folder under library):",
        )
        if not ok:
            return
        name = str(name).strip()
        if not name:
            QMessageBox.warning(
                self,
                "Invalid name",
                "Assembly name cannot be empty.",
            )
            return
        base = root / name
        folder = base
        counter = 1
        while folder.exists() and not folder.is_dir():
            folder = root / f"{name}_{counter}"
            counter += 1
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            QMessageBox.critical(
                self,
                "Error",
                f"Unable to create folder: {folder}",
            )
            return
        label = f"Assembly: {folder.name}"
        metadata = {
            "kind": "assembly",
            "components": [],
            "project": folder.name,
        }
        existing = self._asset_service.get_asset_by_path(str(folder))
        if existing is None:
            created = self._asset_service.create_asset(
                str(folder),
                label=label,
                metadata=metadata,
            )
        else:
            created = self._asset_service.update_asset(
                existing.id,
                label=label,
                metadata=metadata,
            )
        self._populate_repository()
        self._show_assembly(created)
        try:
            repo_container = getattr(self, "_repo_container", None)
            if repo_container is not None and repo_container.isVisible():
                for row in range(self._repository_list.count()):
                    it = self._repository_list.item(row)
                    item_path = str(it.data(Qt.UserRole) or it.text())
                    if item_path == str(folder):
                        self._repository_list.setCurrentItem(it)
                        break
        except Exception:
            pass

    def _create_new_part(self) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        asset = self._current_asset
        if asset is None or not isinstance(asset.metadata, dict):
            QMessageBox.information(
                self,
                "No Assembly",
                "Select or open an assembly first.",
            )
            return
        kind = str(asset.metadata.get("kind") or "").lower()
        if kind != "assembly":
            QMessageBox.information(
                self,
                "No Assembly",
                "Select or open an assembly first.",
            )
            return
        folder = Path(asset.path).expanduser().resolve()
        name, ok = QInputDialog.getText(
            self,
            "New Part",
            "Part name (folder under assembly):",
        )
        if not ok:
            return
        name = str(name).strip()
        if not name:
            QMessageBox.warning(
                self,
                "Invalid name",
                "Part name cannot be empty.",
            )
            return
        part_dir = folder / name
        counter = 1
        while part_dir.exists() and not part_dir.is_dir():
            part_dir = folder / f"{name}_{counter}"
            counter += 1
        try:
            part_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            QMessageBox.critical(
                self,
                "Error",
                f"Unable to create part folder: {part_dir}",
            )
            return
        self._create_or_update_assembly(folder)
        parent_asset = self._asset_service.get_asset_by_path(str(folder))
        if parent_asset is not None:
            self._show_assembly(parent_asset)
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(part_dir)))
        except Exception:
            pass


def main() -> int:
    """Launch the 3dfs Qt application."""

    app = QApplication.instance()
    owns_application = False

    if app is None:
        app = QApplication(sys.argv)
        owns_application = True

    window = MainWindow()
    window.show()

    if owns_application:
        return app.exec()

    return 0
