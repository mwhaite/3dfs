"""Main Qt window for the 3dfs desktop shell."""

from __future__ import annotations

import logging
import mimetypes
import os
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication, QFileSystemWatcher, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenuBar,
    QSplitter,
    QWidget,
)

from ..config import configure, get_config
from ..customizer.pipeline import PipelineResult
from ..data import TagStore
from ..importer import SUPPORTED_EXTENSIONS
from ..project import build_attachment_metadata
from ..search import LibrarySearch
from ..storage import AssetService
from ..ui import PreviewPane, ProjectPane, SettingsDialog, TagSidebar
from .project_scanner import (
    ProjectRefreshRequest,
    ProjectScanOutcome,
    ProjectScanWorker,
)
from .settings import (
    APPLICATION_NAME,
    ORGANIZATION_NAME,
    AppSettings,
    load_app_settings,
    save_app_settings,
)

WINDOW_TITLE = "3dfs"

logger = logging.getLogger(__name__)

__all__ = ["MainWindow"]


class MainWindow(QMainWindow):
    """Primary window for the 3dfs shell."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)

        QCoreApplication.setOrganizationName(ORGANIZATION_NAME)
        QCoreApplication.setApplicationName(APPLICATION_NAME)

        config = get_config()
        settings = load_app_settings(fallback_root=config.library_root)
        if settings.library_root != config.library_root:
            config = configure(library_root=settings.library_root)

        self._settings: AppSettings = settings
        self._env_bootstrap_demo = bool(os.environ.get("THREE_DFS_BOOTSTRAP_DEMO"))
        self._bootstrap_demo_data = (
            self._env_bootstrap_demo or self._settings.bootstrap_demo_data
        )
        self._auto_refresh_projects = self._settings.auto_refresh_projects

        self._asset_service = AssetService()
        self._library_search = LibrarySearch(service=self._asset_service)
        self._tag_store = TagStore(service=self._asset_service)
        self._tag_sidebar = TagSidebar(
            self._tag_store, asset_service=self._asset_service
        )
        self._repository_list = QListWidget(self)
        self._repository_list.setObjectName("repositoryList")
        self._repository_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._toggle_repo_action: QAction | None = None
        # Right-click context menu on repository list
        self._repository_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._repository_list.customContextMenuRequested.connect(
            self._show_repo_context_menu
        )

        self._preview_pane = PreviewPane(
            base_path=config.library_root,
            asset_service=self._asset_service,
            parent=self,
        )
        self._preview_pane.set_text_preview_limit(self._settings.text_preview_limit)
        self._preview_pane.setObjectName("previewPane")
        self._preview_pane.navigationRequested.connect(self._handle_preview_navigation)
        self._preview_pane.customizationGenerated.connect(
            self._handle_customization_generated
        )

        # Project pane shares the right split area via a stacked layout
        self._project_pane = ProjectPane(self)
        self._project_pane.setObjectName("projectPane")
        # Wire project pane actions
        self._project_pane.newPartRequested.connect(self._create_new_part)
        self._project_pane.addAttachmentsRequested.connect(
            self._add_project_attachments
        )
        self._project_pane.openFolderRequested.connect(
            self._open_current_project_folder
        )
        self._project_pane.openItemFolderRequested.connect(self._open_item_folder)
        self._project_pane.navigateUpRequested.connect(self._navigate_up_project)
        self._project_pane.navigateToPathRequested.connect(self._navigate_to_path)
        self._project_pane.filesDropped.connect(
            self._add_project_attachments_from_files
        )
        self._project_pane.refreshRequested.connect(self._refresh_current_project)

        # File system watcher for live project refresh
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(self._on_fs_changed)
        self._fs_watcher.fileChanged.connect(self._on_fs_changed)
        self._fs_debounce = QTimer(self)
        self._fs_debounce.setSingleShot(True)
        self._fs_debounce.setInterval(400)
        self._fs_debounce.timeout.connect(self._refresh_current_project)
        self._watched_dirs: set[str] = set()
        self._thread_pool = QThreadPool.globalInstance()
        self._project_workers: dict[str, ProjectScanWorker] = {}
        self._project_pending: dict[str, int] = {}
        self._project_refresh_requests: dict[str, ProjectRefreshRequest] = {}

        self._build_layout()
        self._connect_signals()
        self._build_menu()
        self._populate_repository()
        self._current_asset = None
        # If nothing is persisted yet, attempt an initial rescan to discover
        # assets already present in the configured library directory.
        if self._repository_list.count() == 0:
            self._rescan_library()
        # Apply persisted interface preferences
        self._toggle_repository_sidebar(self._settings.show_repository_sidebar)

    def _is_safe_path_string(self, path_str: str) -> bool:
        """Validate that a path string is safe before any Path operations."""
        try:
            # Basic type and length checks
            if not isinstance(path_str, str):
                return False
            if len(path_str) > 4096:
                return False
            if not path_str.strip():
                return False
            
            # Check for null bytes and other problematic characters
            if '\x00' in path_str or '\r' in path_str:
                return False
            
            # Check for excessive repetition that might indicate circular references
            normalized = path_str.replace('\\', '/')
            
            # Count path separators - too many might indicate a problem
            if normalized.count('/') > 100:
                return False
            
            # Check for repeated patterns that suggest circular references
            if '..' in normalized:
                dotdot_count = normalized.count('..')
                if dotdot_count > 50:
                    return False
            
            # Check for excessive repetition of the same substring
            if len(normalized) > 500:
                # Look for repeated substrings that might indicate circular references
                for length in [10, 20, 50]:
                    if len(normalized) > length * 10:
                        for i in range(len(normalized) - length):
                            substr = normalized[i:i+length]
                            if normalized.count(substr) > 10:
                                return False
            
            # Try a basic Path operation to see if it causes recursion
            # Use a timeout-like approach by limiting the string length we test
            test_str = normalized[:1000]  # Limit test to reasonable length
            try:
                # This is the critical test - if this causes recursion, reject the path
                from pathlib import Path
                test_path = Path(test_str)
                _ = str(test_path)  # Force evaluation
                _ = test_path.parts  # Test parts access
                return True
            except (RecursionError, ValueError, OSError):
                return False
                
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Layout & wiring
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        from PySide6.QtWidgets import (
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QStackedWidget,
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

        self._detail_stack = QStackedWidget(central_widget)
        self._detail_stack.addWidget(self._preview_pane)  # index 0
        self._detail_stack.addWidget(self._project_pane)  # index 1

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
        # By default show only persisted assets; opt-in demo seeding via settings.
        if self._bootstrap_demo_data:
            assets = self._asset_service.bootstrap_demo_data()
        else:
            assets = self._asset_service.list_assets()

        valid_assets = 0
        for asset in assets:
            # DEBUG: Log the raw asset path to find the source of corruption
            logger.debug("Processing asset: id=%s, path=%r, path_type=%s, path_len=%d", 
                        asset.id, asset.path, type(asset.path), len(asset.path))
            
            # FUNDAMENTAL FIX: Validate asset paths before adding to UI
            if not self._is_safe_path_string(asset.path):
                logger.error("CORRUPTED PATH DETECTED: asset_id=%s, path_repr=%r, path_len=%d", 
                           asset.id, asset.path, len(asset.path))
                # Try to understand what's in the path
                if len(asset.path) > 1000:
                    logger.error("Path sample (first 500 chars): %r", asset.path[:500])
                    logger.error("Path sample (last 500 chars): %r", asset.path[-500:])
                continue
                
            display_label = asset.label or asset.path
            item = QListWidgetItem(display_label)
            item.setData(Qt.UserRole, asset.path)
            item.setToolTip(asset.path)
            self._repository_list.addItem(item)
            valid_assets += 1

        if self._repository_list.count():
            self._repository_list.setCurrentRow(0)
        else:
            # Surface the current library root to help users locate files.
            self.statusBar().showMessage(f"Library: {get_config().library_root}", 5000)
        
        if valid_assets < len(assets):
            skipped = len(assets) - valid_assets
            self.statusBar().showMessage(f"Loaded {valid_assets} assets, skipped {skipped} with invalid paths", 3000)

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

        file_menu.addSeparator()

        settings_action = QAction("Settings…", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._open_settings_dialog)
        file_menu.addAction(settings_action)

        # Project actions
        project_menu = menubar.addMenu("&Projects")
        from PySide6.QtWidgets import QFileDialog

        def _new_project_from_folder() -> None:
            config = get_config()
            folder = QFileDialog.getExistingDirectory(
                self,
                "Choose project folder",
                str(config.library_root),
            )
            if not folder:
                return
            self._create_or_update_project(Path(folder))

        new_project_action = QAction("New Project From Folder…", self)
        new_project_action.triggered.connect(_new_project_from_folder)
        project_menu.addAction(new_project_action)

        new_empty_project_action = QAction("New Empty Project…", self)
        new_empty_project_action.triggered.connect(self._new_empty_project_dialog)
        project_menu.addAction(new_empty_project_action)

        new_part_action = QAction("New Part in Current Project…", self)
        new_part_action.triggered.connect(self._create_new_part)
        project_menu.addAction(new_part_action)

        add_attachment_action = QAction("Add Attachment(s) to Current Project…", self)
        add_attachment_action.triggered.connect(self._add_project_attachments)
        project_menu.addAction(add_attachment_action)

        organize_parts_action = QAction("Organize Parts Into Folders", self)
        organize_parts_action.triggered.connect(self._organize_parts_into_folders)
        project_menu.addAction(organize_parts_action)

        organize_action = QAction("Organize Library", self)
        organize_action.triggered.connect(self._organize_library)
        project_menu.addAction(organize_action)

        sidebar_menu = menubar.addMenu("&View")
        toggle_repo_action = QAction("Toggle Repository Sidebar", self)
        toggle_repo_action.setShortcut("Ctrl+R")
        toggle_repo_action.setCheckable(True)
        toggle_repo_action.toggled.connect(self._toggle_repository_sidebar)
        sidebar_menu.addAction(toggle_repo_action)
        toggle_repo_action.setChecked(self._settings.show_repository_sidebar)
        self._toggle_repo_action = toggle_repo_action

    def _open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self._settings, self)
        if dialog.exec() != QDialog.Accepted:
            return
        new_settings = dialog.result_settings()
        if new_settings is None:
            return
        self._apply_settings(new_settings)

    def _apply_settings(self, new_settings: AppSettings) -> None:
        previous = self._settings
        self._settings = new_settings
        save_app_settings(new_settings)

        previous_demo_state = (
            self._env_bootstrap_demo or previous.bootstrap_demo_data
        )
        current_demo_state = (
            self._env_bootstrap_demo or new_settings.bootstrap_demo_data
        )
        self._bootstrap_demo_data = current_demo_state
        if previous_demo_state != current_demo_state:
            self._populate_repository()

        if new_settings.library_root != previous.library_root:
            config = configure(library_root=new_settings.library_root)
            self._preview_pane.set_base_path(config.library_root)
            self._populate_repository()
            self.statusBar().showMessage(f"Library: {config.library_root}", 5000)

        if new_settings.show_repository_sidebar != previous.show_repository_sidebar:
            toggle_action = getattr(self, "_toggle_repo_action", None)
            if toggle_action is not None:
                toggle_action.setChecked(new_settings.show_repository_sidebar)
            else:
                self._toggle_repository_sidebar(new_settings.show_repository_sidebar)

        if new_settings.auto_refresh_projects != previous.auto_refresh_projects:
            self._auto_refresh_projects = new_settings.auto_refresh_projects
            self._update_project_watchers()

        if new_settings.text_preview_limit != previous.text_preview_limit:
            self._preview_pane.set_text_preview_limit(new_settings.text_preview_limit)
            self._preview_pane.reload_current_preview()

    def _show_repo_context_menu(self, pos) -> None:
        current_item = self._repository_list.currentItem()
        try:
            clicked_item = self._repository_list.itemAt(pos)
        except Exception:
            clicked_item = None
        item = clicked_item or current_item
        if clicked_item is not None and clicked_item is not current_item:
            try:
                self._repository_list.setCurrentItem(clicked_item)
            except Exception:
                pass
            item = clicked_item
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        customize_act = None
        if item is not None and self._preview_pane.can_customize:
            customize_act = menu.addAction("Customize…")
            menu.addSeparator()
        new_project_act = menu.addAction("New Empty Project…")
        open_project_act = menu.addAction("Open as Project")
        open_folder_act = menu.addAction("Open Containing Folder")
        has_selection = item is not None
        open_project_act.setEnabled(has_selection)
        open_folder_act.setEnabled(has_selection)
        global_pos = self._repository_list.mapToGlobal(pos)
        action = menu.exec(global_pos)
        if action is None:
            return
        if action == customize_act:
            self._preview_pane.launch_customizer()
            return
        if action == new_project_act:
            self._new_empty_project_dialog()
            return
        if action == open_project_act:
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
                self._create_or_update_project(
                    folder,
                    select_in_repo=True,
                    show_project=True,
                )
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

    def _toggle_repository_sidebar(self, visible: bool) -> None:
        container = getattr(self, "_repo_container", None)
        if container is None:
            return
        container.setVisible(bool(visible))

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

                project_name = derive_project_name(source)
                project_dir = root / project_name
                project_dir.mkdir(parents=True, exist_ok=True)

                destination = project_dir / source.name
                if destination.exists():
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
        
        # DEBUG: Log selection details
        logger.debug("Selection change: item_id=%r, item_id_type=%s, item_id_len=%d", 
                    item_id, type(item_id), len(item_id))
        
        # FUNDAMENTAL FIX: Validate path data at the source
        if not self._is_safe_path_string(item_id):
            logger.error("CORRUPTED PATH IN SELECTION: item_id=%r, len=%d", item_id, len(item_id))
            if len(item_id) > 1000:
                logger.error("Selection path sample (first 500): %r", item_id[:500])
                logger.error("Selection path sample (last 500): %r", item_id[-500:])
            self._preview_pane.clear()
            self._tag_sidebar.set_active_item(None)
            self.statusBar().showMessage("Invalid path data detected - skipping selection", 5000)
            return
        
        asset = self._asset_service.get_asset_by_path(item_id)

        # Project detection: assets with kind == 'project' in metadata
        if (
            asset is not None
            and isinstance(asset.metadata, dict)
            and (str(asset.metadata.get("kind") or "").lower() == "project")
        ):
            self._show_project(asset)
            return

        # Unify: if selection is a file, show its folder as project context
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

            # Load/show project for parent and select this file inside
            self._create_or_update_project(
                parent,
                select_in_repo=True,
                focus_component=str(target),
                show_project=True,
            )
            for row in range(self._repository_list.count()):
                it = self._repository_list.item(row)
                if str(it.data(Qt.UserRole) or it.text()) == str(parent):
                    self._repository_list.setCurrentItem(it)
                    break
            parent_asset = self._asset_service.get_asset_by_path(str(parent))
            if parent_asset is not None:
                self._show_project(parent_asset)
                try:
                    self._project_pane.select_item(str(target))
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

        self._detail_stack.setCurrentWidget(self._preview_pane)
        self._tag_sidebar.set_active_item(item_id)
        self._update_project_watchers()

    def _show_project(self, asset) -> None:
        # Build component list from metadata["components"] entries
        meta = dict(asset.metadata or {})
        comps_raw = meta.get("components") or []

        from ..ui.project_pane import ProjectArrangement, ProjectComponent

        comp_objs: list[ProjectComponent] = []
        for entry in comps_raw:
            if not isinstance(entry, dict):
                continue
            try:
                raw_path = entry.get("path")
                path = str(raw_path or "").strip()
            except Exception:
                continue
            if not path:
                continue
            raw_label = entry.get("label")
            try:
                label = str(raw_label).strip() if raw_label is not None else ""
            except Exception:
                label = ""
            if not label:
                try:
                    label = Path(path).name
                except Exception:
                    label = path
            try:
                kind = str(entry.get("kind") or "component")
            except Exception:
                kind = "component"
            metadata_entry = entry.get("metadata")
            metadata_dict = metadata_entry if isinstance(metadata_entry, dict) else None
            asset_id_value = entry.get("asset_id")
            try:
                asset_id = int(asset_id_value)
            except Exception:
                asset_id = None
            resolved_kind = (
                kind if kind in {"component", "placeholder"} else "component"
            )
            comp_objs.append(
                ProjectComponent(
                    path=path,
                    label=label,
                    kind=resolved_kind,
                    metadata=metadata_dict,
                    asset_id=asset_id,
                )
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
                ProjectArrangement(
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
            metadata_entry = a.get("metadata")
            metadata_dict = metadata_entry if isinstance(metadata_entry, dict) else None
            att_objs.append(
                ProjectComponent(
                    path=path_value,
                    label=label_text,
                    kind="attachment",
                    metadata=metadata_dict,
                )
            )

        self._project_pane.set_project(
            asset.path,
            label=asset.label,
            components=comp_objs,
            arrangements=arr_objs,
            attachments=att_objs,
        )
        self._detail_stack.setCurrentWidget(self._project_pane)
        self._tag_sidebar.set_active_item(asset.path)
        self._current_asset = asset
        self._update_project_watchers()

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
        active_item = self._tag_sidebar.active_item()
        if active_item:
            self._tag_sidebar.set_active_item(active_item)

    def _apply_repository_filters(self) -> None:
        raw_text = (
            self._repo_search_input.text()
            if hasattr(self, "_repo_search_input")
            else ""
        )
        query = raw_text.strip()
        text_needle = query.casefold()
        tag_matches = getattr(self, "_tag_filter_matches", None)
        search_paths = self._run_library_search(query) if query else None

        for row in range(self._repository_list.count()):
            item = self._repository_list.item(row)
            path = str(item.data(Qt.UserRole) or item.text())
            label = item.text()
            matches_tag = True if tag_matches is None else (path in tag_matches)
            if search_paths is None:
                if not text_needle:
                    matches_text = True
                else:
                    label_case = (label or "").casefold()
                    matches_text = (
                        text_needle in label_case or text_needle in path.casefold()
                    )
            else:
                matches_text = path in search_paths
            item.setHidden(not (matches_tag and matches_text))

    def _run_library_search(self, query: str) -> set[str] | None:
        """Return asset paths that match *query* using :mod:`three_dfs.search`."""

        try:
            hits = self._library_search.search(query)
        except Exception:
            logger.exception("Failed to execute library search", exc_info=True)
            return None

        matches: set[str] = set()
        for hit in hits:
            target = hit.project_path or hit.path
            if target:
                matches.add(target)
        return matches

    # ------------------------------------------------------------------
    # Project helpers
    # ------------------------------------------------------------------
    def _create_or_update_project(
        self,
        folder: Path,
        *,
        select_in_repo: bool = False,
        focus_component: str | None = None,
        show_project: bool = False,
    ) -> None:
        folder = folder.expanduser().resolve()
        config = get_config()
        root = config.library_root
        try:
            folder.relative_to(root)
        except Exception:
            self.statusBar().showMessage("Folder must be under the library root", 4000)
            return

        key = str(folder)
        request = self._project_refresh_requests.get(key)
        if request is None:
            request = ProjectRefreshRequest()
            self._project_refresh_requests[key] = request
        if select_in_repo:
            request.select_in_repo = True
        if show_project or focus_component is not None:
            request.show_project = True
        if focus_component is not None:
            request.focus_component = focus_component

        if key in self._project_workers:
            self._project_pending[key] = self._project_pending.get(key, 0) + 1
            return

        existing = self._asset_service.get_asset_by_path(str(folder))
        worker = ProjectScanWorker(folder, self._asset_service, existing)
        worker.signals.finished.connect(self._handle_project_scan_finished)
        worker.signals.error.connect(self._handle_project_scan_error)
        self._project_workers[key] = worker
        self.statusBar().showMessage(
            f"Updating project '{folder.name}'…",
            1500,
        )
        self._thread_pool.start(worker)

    def _handle_project_scan_finished(self, payload: object) -> None:
        outcome = payload if isinstance(payload, ProjectScanOutcome) else None
        if outcome is None:
            return

        key = str(outcome.folder)
        self._project_workers.pop(key, None)
        pending = self._project_pending.pop(key, 0)
        request = self._project_refresh_requests.pop(key, None)

        self._populate_repository()
        name = outcome.folder.name
        self.statusBar().showMessage(
            f"Project '{name}' updated with {outcome.component_count} component(s)",
            4000,
        )

        if request is not None and request.select_in_repo:
            self._select_repository_path(key)

        should_show = False
        focus_component = None
        if request is not None:
            should_show = request.show_project
            focus_component = request.focus_component

        current_path = None
        if self._current_asset is not None:
            try:
                current_path = Path(self._current_asset.path).expanduser().resolve()
            except Exception:  # noqa: BLE001
                current_path = None
        if current_path == outcome.folder:
            should_show = True

        if should_show:
            self._show_project(outcome.asset)
            if focus_component:
                try:
                    self._project_pane.select_item(focus_component)
                except Exception:  # noqa: BLE001
                    pass
        else:
            if self._current_asset is not None and str(self._current_asset.path) == key:
                self._current_asset = outcome.asset

        self._update_project_watchers()

        if pending > 0:
            remaining = pending - 1
            if remaining > 0:
                self._project_pending[key] = remaining
            self._create_or_update_project(outcome.folder)

    def _handle_project_scan_error(self, folder_path: str, message: str) -> None:
        self._project_workers.pop(folder_path, None)
        pending = self._project_pending.pop(folder_path, 0)
        self._project_refresh_requests.pop(folder_path, None)

        folder_name = Path(folder_path).name
        self.statusBar().showMessage(
            f"Failed to update project '{folder_name}': {message}",
            5000,
        )

        if pending > 0:
            try:
                self._create_or_update_project(Path(folder_path))
            except Exception:  # noqa: BLE001
                logger.exception("Retrying project refresh failed for %s", folder_path)

    def _add_project_attachments(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        files, _ = QFileDialog.getOpenFileNames(self, "Select attachment files")
        if not files:
            return
        self._add_project_attachments_from_files(files)

    def _add_project_attachments_from_files(self, files: list[str]) -> None:
        asset = self._current_asset
        if asset is None or not isinstance(asset.metadata, dict):
            self.statusBar().showMessage("Select a project to add attachments.", 3000)
            return
        kind = str(asset.metadata.get("kind") or "").lower()
        if kind != "project":
            self.statusBar().showMessage("Select a project to add attachments.", 3000)
            return

        project_folder = Path(asset.path).expanduser().resolve()
        if not project_folder.is_dir():
            self.statusBar().showMessage("Project path is not a folder on disk.", 3000)
            return

        # Target: if a component is selected, attach into that component's folder;
        # otherwise, attach into the project folder root.
        attachments_dir = project_folder
        try:
            selected = self._project_pane.selected_item()
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

        added: list[dict[str, Any]] = []
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
                entry["rel_path"] = str(dest.relative_to(project_folder))
            except Exception:
                pass
            entry["metadata"] = build_attachment_metadata(
                dest,
                project_root=project_folder,
                source_path=source,
            )
            added.append(entry)

        if not added:
            self.statusBar().showMessage("No attachments were added.", 3000)
            return

        meta = dict(asset.metadata or {})
        existing = list(meta.get("attachments") or [])
        meta["attachments"] = existing + added
        refreshed = self._asset_service.update_asset(asset.id, metadata=meta)
        self._show_project(refreshed)
        self.statusBar().showMessage(f"Added {len(added)} attachment(s)", 4000)

    def _organize_parts_into_folders(self) -> None:
        asset = self._current_asset
        if asset is None or not isinstance(asset.metadata, dict):
            self.statusBar().showMessage("Select a project to organize parts.", 3000)
            return
        kind = str(asset.metadata.get("kind") or "").lower()
        if kind != "project":
            self.statusBar().showMessage("Select a project to organize parts.", 3000)
            return
        folder = Path(asset.path).expanduser().resolve()
        if not folder.is_dir():
            self.statusBar().showMessage("Project path is not a folder on disk.", 3000)
            return

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
        self._create_or_update_project(folder, show_project=True)
        self.statusBar().showMessage(f"Organized parts: {moved} moved", 4000)

    def _open_current_project_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        asset = self._current_asset
        if asset is None or not isinstance(asset.metadata, dict):
            self.statusBar().showMessage("Select a project to open its folder.", 3000)
            return
        kind = str(asset.metadata.get("kind") or "").lower()
        if kind != "project":
            self.statusBar().showMessage("Select a project to open its folder.", 3000)
            return
        folder = Path(asset.path).expanduser()
        if not folder.exists():
            self.statusBar().showMessage(
                "Project folder does not exist on disk.",
                3000,
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ------------------------------------------------------------------
    # Project refresh helpers
    # ------------------------------------------------------------------
    def _refresh_current_project(self) -> None:
        asset = self._current_asset
        if asset is None:
            return
        if not isinstance(asset.metadata, dict):
            return
        kind = str(asset.metadata.get("kind") or "").lower()
        if kind != "project":
            return
        folder = Path(asset.path)
        self._create_or_update_project(folder, show_project=True)

    def _watch_project_folder(self, folder: Path) -> None:
        # Reset watchers to only current project folder
        try:
            if self._watched_dirs:
                self._fs_watcher.removePaths(list(self._watched_dirs))
        except Exception:
            pass
        self._watched_dirs = set()
        if not self._auto_refresh_projects:
            return
        if folder.exists():
            self._fs_watcher.addPath(str(folder))
            self._watched_dirs.add(str(folder))

    def _on_fs_changed(self, changed_path: str) -> None:
        # Debounce refresh bursts
        if not self._auto_refresh_projects:
            return
        self._fs_debounce.start()

    def _update_project_watchers(self) -> None:
        if not self._auto_refresh_projects:
            try:
                if self._watched_dirs:
                    self._fs_watcher.removePaths(list(self._watched_dirs))
            except Exception:
                pass
            self._watched_dirs = set()
            return

        asset = self._current_asset
        if (
            asset is None
            or not isinstance(asset.metadata, dict)
            or str(asset.metadata.get("kind") or "").lower() != "project"
        ):
            try:
                if self._watched_dirs:
                    self._fs_watcher.removePaths(list(self._watched_dirs))
            except Exception:
                pass
            self._watched_dirs = set()
            return

        try:
            self._watch_project_folder(Path(asset.path))
        except Exception:
            pass

    def _open_item_folder(self, item_path: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        folder = Path(item_path).expanduser().parent
        if folder.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _navigate_up_project(self) -> None:
        asset = self._current_asset
        if asset is None or not isinstance(asset.metadata, dict):
            self.statusBar().showMessage("Select a project to navigate.", 3000)
            return
        kind = str(asset.metadata.get("kind") or "").lower()
        if kind != "project":
            self.statusBar().showMessage("Select a project to navigate.", 3000)
            return
        folder = Path(asset.path).expanduser().resolve()
        parent = folder.parent
        try:
            parent.relative_to(get_config().library_root)
        except Exception:
            self.statusBar().showMessage("Cannot navigate outside library root.", 3000)
            return
        self._create_or_update_project(parent, show_project=True)
        parent_asset = self._asset_service.get_asset_by_path(str(parent))
        if parent_asset is not None:
            self._show_project(parent_asset)
        self._select_repository_path(str(parent))

    def _navigate_to_path(self, target: str) -> None:
        try:
            folder = Path(target).expanduser().resolve()
        except Exception:
            return
        try:
            folder.relative_to(get_config().library_root)
        except Exception:
            return
        self._create_or_update_project(
            folder,
            select_in_repo=True,
            show_project=True,
        )
        for row in range(self._repository_list.count()):
            item = self._repository_list.item(row)
            item_path = str(item.data(Qt.UserRole) or item.text())
            if item_path == str(folder):
                self._repository_list.setCurrentItem(item)
                break

    def _select_repository_path(self, path: str) -> None:
        for row in range(self._repository_list.count()):
            item = self._repository_list.item(row)
            data = str(item.data(Qt.UserRole) or item.text())
            if data == path:
                self._repository_list.setCurrentItem(item)
                break

    def _handle_tags_changed(self, path: str, tags: list[str]) -> None:
        del tags  # unused but present in signal signature
        asset = self._asset_service.get_asset_by_path(path)
        if asset is None:
            return
        if self._current_asset is not None and self._current_asset.id == asset.id:
            self._current_asset = asset
            self._update_project_watchers()
        self._populate_repository()
        self._select_repository_path(path)

    def _handle_preview_navigation(self, target: str) -> None:
        path = Path(target)
        if path.is_dir():
            self._create_or_update_project(path, show_project=True, select_in_repo=True)
            asset = self._asset_service.get_asset_by_path(str(path))
            if asset is not None:
                self._show_project(asset)
            return
        asset = self._asset_service.get_asset_by_path(str(path))
        if asset is not None:
            self._preview_pane.set_item(
                asset.path,
                label=asset.label,
                metadata=asset.metadata,
                asset_record=asset,
            )
            self._detail_stack.setCurrentWidget(self._preview_pane)
            self._tag_sidebar.set_active_item(asset.path)
            self._current_asset = asset
            self._update_project_watchers()

    def _handle_customization_generated(self, result: PipelineResult) -> None:
        asset_path = result.output_path
        asset = self._asset_service.ensure_asset(
            asset_path, label=Path(asset_path).name
        )
        metadata = dict(asset.metadata or {})
        metadata.setdefault("kind", "generated")
        metadata.setdefault("source_customization", result.customization_id)
        metadata.setdefault("parameters", result.parameters)
        metadata.setdefault("generated_at", result.generated_at.isoformat())
        updated = self._asset_service.update_asset(asset.id, metadata=metadata)
        self._populate_repository()
        self._preview_pane.set_item(
            updated.path,
            label=updated.label,
            metadata=updated.metadata,
            asset_record=updated,
        )
        self._detail_stack.setCurrentWidget(self._preview_pane)
        self._current_asset = updated
        self._update_project_watchers()
        self.statusBar().showMessage("Customization output recorded", 4000)

    def _rescan_library(self) -> None:
        config = get_config()
        root = config.library_root.expanduser().resolve()
        if not root.exists():
            self.statusBar().showMessage("Library root does not exist on disk.", 4000)
            return

        discovered: list[Path] = []
        for entry in root.iterdir():
            if entry.is_dir():
                discovered.append(entry)

        if not discovered:
            self.statusBar().showMessage(
                "No projects discovered under library root.", 4000
            )
            return

        for folder in discovered:
            self._create_or_update_project(folder)

    def _new_empty_project_dialog(self) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        root = get_config().library_root
        name, ok = QInputDialog.getText(
            self,
            "New Project",
            "Project name (folder under library):",
        )
        if not ok:
            return
        name = str(name).strip()
        if not name:
            QMessageBox.warning(
                self,
                "Invalid name",
                "Project name cannot be empty.",
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

        # Create project asset with empty components
        label = f"Project: {folder.name}"
        metadata = {
            "kind": "project",
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

        # Refresh and show the new project
        self._populate_repository()
        self._show_project(created)
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

    def _create_new_part(self) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        asset = self._current_asset
        if asset is None or not isinstance(asset.metadata, dict):
            QMessageBox.information(
                self,
                "No Project",
                "Select or open a project first.",
            )
            return
        kind = str(asset.metadata.get("kind") or "").lower()
        if kind != "project":
            QMessageBox.information(
                self,
                "No Project",
                "Select or open a project first.",
            )
            return
        folder = Path(asset.path).expanduser().resolve()
        name, ok = QInputDialog.getText(
            self,
            "New Part",
            "Part name (folder under project):",
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
        self._create_or_update_project(
            folder,
            show_project=True,
            focus_component=str(part_dir),
        )
        parent_asset = self._asset_service.get_asset_by_path(str(folder))
        if parent_asset is not None:
            self._show_project(parent_asset)
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(part_dir)))
        except Exception:
            pass
