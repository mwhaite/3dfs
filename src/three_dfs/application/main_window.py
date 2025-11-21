"""Main Qt window for the 3dfs desktop shell."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from PySide6.QtCore import QCoreApplication, QFileSystemWatcher, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QListWidget,
    QMainWindow,
    QSplitter,
    QToolButton,
    QWidget,
)

from ..config import configure, get_config
from ..search import LibrarySearch
from ..storage import AssetService
from ..ui.container_pane import ContainerPane
from ..ui.preview_pane import PreviewPane
from ..ui.settings_dialog import SettingsDialog
from ..ui.tag_graph import TagGraphPane
from ..ui.tag_sidebar import TagSidebar
from ..ui.widgets import RepositoryListWidget
from ..ui.delegates import StarDelegate
from .asset_manager import AssetManager
from .container_manager import ContainerManager
from .container_scanner import (
    ContainerRefreshRequest,
    ContainerScanWorker,
)
from .library_manager import LibraryManager
from .menu_manager import MenuManager
from .settings import (
    APPLICATION_NAME,
    ORGANIZATION_NAME,
    AppSettings,
    load_app_settings,
    save_app_settings,
)
from .ui_manager import UIManager
from .undo_manager import UndoManager


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
        self._bootstrap_demo_data = self._env_bootstrap_demo or self._settings.bootstrap_demo_data
        self._auto_refresh_containers = self._settings.auto_refresh_containers

        self._undo_manager = UndoManager(self)
        self._container_manager = ContainerManager(self)
        self._library_manager = LibraryManager(self)
        self._ui_manager = UIManager(self)
        self._menu_manager = MenuManager(self)
        self._asset_manager = AssetManager(self)

        self._asset_service = AssetService()
        self._library_search = LibrarySearch(service=self._asset_service)
        self._repository_list = RepositoryListWidget(self, self)
        self._repository_list.setObjectName("repositoryList")
        self._repository_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._repository_list.setItemDelegate(StarDelegate(self._repository_list))
        self._toggle_repo_action: QAction | None = None
        self._tag_panel_action: QAction | None = None
        # Right-click context menu on repository list
        self._repository_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._repository_list.customContextMenuRequested.connect(self._show_repo_context_menu)

        self._preview_pane = PreviewPane(
            base_path=config.library_root,
            asset_service=self._asset_service,
            parent=self,
        )
        self._preview_pane.set_text_preview_limit(self._settings.text_preview_limit)
        self._preview_pane.setObjectName("previewPane")
        self._preview_pane.navigationRequested.connect(self._handle_preview_navigation)
        self._preview_pane.customizationGenerated.connect(self._handle_customization_generated)
        self._preview_pane.tagFilterRequested.connect(self._handle_tag_filter_request)

        # Container pane shares the right split area via a stacked layout
        self._container_pane = ContainerPane(self, asset_service=self._asset_service)
        self._container_pane.setObjectName("containerPane")
        # Wire container pane actions
        self._container_pane.addAttachmentsRequested.connect(self._add_container_attachments)
        self._container_pane.openFolderRequested.connect(self._open_current_container_folder)
        self._container_pane.openItemFolderRequested.connect(self._open_item_folder)
        self._container_pane.backRequested.connect(self._handle_back_requested)
        self._container_pane.linkContainerRequested.connect(self._link_container_into_current_container)
        self._container_pane.importLinkedComponentRequested.connect(self._import_component_from_linked_container)
        self._container_pane.navigateToPathRequested.connect(self._navigate_to_path)
        self._container_pane.filesDropped.connect(self._add_container_attachments_from_files)
        self._container_pane.refreshRequested.connect(self._refresh_current_container)
        self._container_pane.setPrimaryComponentRequested.connect(self._set_primary_component)
        self._container_pane.tagFilterRequested.connect(self._handle_tag_filter_request)
        self._container_pane.versionSelected.connect(self._handle_container_version_selected)
        self._container_pane.createVersionRequested.connect(self._create_container_version_snapshot)
        self._container_pane.manageVersionsRequested.connect(self._manage_container_versions)

        self._tag_graph_pane = TagGraphPane(self)
        self._tag_graph_pane.setObjectName("tagGraphPane")
        self._tag_graph_pane.refreshRequested.connect(self._show_tag_graph)
        self._tag_graph_pane.closeRequested.connect(self._close_tag_graph)
        self._tag_graph_pane.tagFilterRequested.connect(self._handle_tag_graph_tag_filter)

        # File system watcher for live container refresh
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(self._on_fs_changed)
        self._fs_watcher.fileChanged.connect(self._on_fs_changed)
        self._fs_debounce = QTimer(self)
        self._fs_debounce.setSingleShot(True)
        self._fs_debounce.setInterval(400)
        self._fs_debounce.timeout.connect(self._refresh_current_container)
        self._watched_dirs: set[str] = set()
        self._thread_pool = QThreadPool.globalInstance()
        self._container_workers: dict[str, ContainerScanWorker] = {}
        self._container_pending: dict[str, int] = {}
        self._container_refresh_requests: dict[str, ContainerRefreshRequest] = {}

        self._current_asset = None
        self._tag_filter: str | None = None
        self._tag_filter_container_ids: set[int] = set()
        self._tag_filter_order_ids: list[int] = []
        self._tag_filter_container_paths: dict[int, str] = {}
        self._tag_filter_focus_map: dict[int, list[str]] = {}
        self._container_history: list[str] = []
        self._current_container_path: str | None = None
        self._current_container_version_id: int | None = None
        self._suppress_history = False

        self._build_layout()
        self._connect_signals()
        self._build_menu()
        self._populate_repository()
        # If nothing is persisted yet, attempt an initial rescan to discover
        # assets already present in the configured library directory.
        if self._repository_list.count() == 0:
            self._rescan_library()
        # Apply persisted interface preferences
        self._toggle_repository_sidebar(self._settings.show_repository_sidebar)
        self._apply_theme_palette(self._settings.resolved_theme_colors())

    def _is_safe_path_string(self, path_str: str) -> bool:
        """Validate that a path string is safe before any Path operations."""
        try:
            if not isinstance(path_str, str):
                return False
            value = path_str.strip()
            if not value:
                return False
            if len(value) > 4096:
                return False
            if "\x00" in value or "\r" in value:
                return False
            return True
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
        self._repo_search_input.textChanged.connect(self._apply_library_filters)
        clear_search_btn = QToolButton()
        clear_search_btn.setText("✕")
        clear_search_btn.setToolTip("Clear search")
        clear_search_btn.clicked.connect(self._clear_library_search)
        search_row.addWidget(search_label)
        search_row.addWidget(self._repo_search_input, 1)
        search_row.addWidget(clear_search_btn)
        repo_layout.addLayout(search_row)
        repo_layout.addWidget(self._repository_list, 4)

        self._tag_panel = TagSidebar(asset_service=self._asset_service, parent=self._repo_container)
        self._tag_panel.tagFilterRequested.connect(self._handle_tag_filter_request)
        self._tag_panel.tagWebRequested.connect(self._show_tag_graph)
        repo_layout.addWidget(self._tag_panel, 1)

        splitter = QSplitter(Qt.Horizontal, central_widget)
        splitter.addWidget(self._repo_container)

        self._detail_stack = QStackedWidget(central_widget)
        self._detail_stack.addWidget(self._preview_pane)  # index 0
        self._detail_stack.addWidget(self._container_pane)  # index 1
        self._detail_stack.addWidget(self._tag_graph_pane)  # index 2

        splitter.addWidget(self._detail_stack)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)

        layout.addWidget(splitter)
        self.setCentralWidget(central_widget)

    def _connect_signals(self) -> None:
        self._repository_list.currentItemChanged.connect(self._handle_selection_change)

    def _populate_repository(self, *args, **kwargs):
        self._library_manager.populate_repository(*args, **kwargs)

    # ------------------------------------------------------------------
    # Menu & actions
    # ------------------------------------------------------------------
    def _build_menu(self, *args, **kwargs):
        self._menu_manager.build_menu(*args, **kwargs)

        edit_menu = self.menuBar().addMenu("&Edit")
        undo_action = QAction("Undo", self)
        undo_action.setShortcut("Ctrl+Z")
        undo_action.triggered.connect(self._undo_manager.undo)
        edit_menu.addAction(undo_action)
        self._undo_action = undo_action

        redo_action = QAction("Redo", self)
        redo_action.setShortcut("Ctrl+Shift+Z")
        redo_action.triggered.connect(self._undo_manager.redo)
        edit_menu.addAction(redo_action)
        self._redo_action = redo_action

        self._undo_manager.stackChanged.connect(self._update_undo_redo_actions)
        self._update_undo_redo_actions()

    def _update_undo_redo_actions(self):
        self._undo_action.setEnabled(self._undo_manager.can_undo())
        self._redo_action.setEnabled(self._undo_manager.can_redo())


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

        previous_demo_state = self._env_bootstrap_demo or previous.bootstrap_demo_data
        current_demo_state = self._env_bootstrap_demo or new_settings.bootstrap_demo_data
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

        if new_settings.auto_refresh_containers != previous.auto_refresh_containers:
            self._auto_refresh_containers = new_settings.auto_refresh_containers
            self._update_container_watchers()

        if new_settings.text_preview_limit != previous.text_preview_limit:
            self._preview_pane.set_text_preview_limit(new_settings.text_preview_limit)
            self._preview_pane.reload_current_preview()

        if (
            new_settings.theme_name != previous.theme_name
            or new_settings.theme_colors != previous.theme_colors
            or new_settings.custom_themes != previous.custom_themes
        ):
            self._apply_theme_palette(new_settings.resolved_theme_colors())

    def _apply_theme_palette(self, colors: Mapping[str, str]) -> None:
        def _color_for(key: str, fallback: str) -> QColor:
            value = colors.get(key, fallback) if isinstance(colors, Mapping) else fallback
            candidate = QColor(value)
            return candidate if candidate.isValid() else QColor(fallback)

        window_color = _color_for("window", "#202124")
        panel_color = _color_for("panel", "#2b2c30")
        accent_color = _color_for("accent", "#5c9cff")
        text_color = _color_for("text", "#f0f0f0")

        palette = self.palette()
        palette.setColor(QPalette.Window, window_color)
        palette.setColor(QPalette.Base, panel_color)
        palette.setColor(QPalette.AlternateBase, panel_color.lighter(115))
        palette.setColor(QPalette.Button, panel_color)
        palette.setColor(QPalette.ButtonText, text_color)
        palette.setColor(QPalette.Text, text_color)
        palette.setColor(QPalette.WindowText, text_color)
        palette.setColor(QPalette.BrightText, text_color)
        palette.setColor(QPalette.ToolTipBase, panel_color)
        palette.setColor(QPalette.ToolTipText, text_color)
        palette.setColor(QPalette.Highlight, accent_color)
        palette.setColor(QPalette.Link, accent_color)
        palette.setColor(QPalette.LinkVisited, accent_color.darker(110))
        self.setPalette(palette)

        stylesheet = f"""
        QWidget {{ color: {text_color.name()}; }}
        QMainWindow, QDialog, QTabWidget::pane {{ background-color: {window_color.name()}; }}
        QGroupBox, QLineEdit, QListWidget, QTreeView, QTableView, QTextEdit, QComboBox, QPushButton {{
            background-color: {panel_color.name()};
            color: {text_color.name()};
            border: 1px solid {panel_color.darker(115).name()};
        }}
        QPushButton:hover, QToolButton:hover {{
            background-color: {accent_color.name()};
            color: {window_color.name()};
        }}
        QLineEdit, QListWidget::item:selected, QTreeView::item:selected, QTableView::item:selected {{
            selection-background-color: {accent_color.name()};
            selection-color: {window_color.name()};
        }}
        """
        self.setStyleSheet(stylesheet)

    def _show_repo_context_menu(self, *args, **kwargs):
        self._menu_manager.show_repo_context_menu(*args, **kwargs)

    def _toggle_repository_sidebar(self, *args, **kwargs):
        self._ui_manager.toggle_repository_sidebar(*args, **kwargs)

    def _handle_back_requested(self, *args, **kwargs):
        self._ui_manager.handle_back_requested(*args, **kwargs)

    def _toggle_tag_panel(self, *args, **kwargs):
        self._ui_manager.toggle_tag_panel(*args, **kwargs)

    def _clear_library_search(self, *args, **kwargs):
        self._ui_manager.clear_library_search(*args, **kwargs)

    def _handle_tag_filter_request(self, *args, **kwargs):
        self._ui_manager.handle_tag_filter_request(*args, **kwargs)

    def _organize_library(self, *args, **kwargs):
        self._library_manager.organize_library(*args, **kwargs)

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------
    def _handle_selection_change(self, *args, **kwargs):
        self._ui_manager.handle_selection_change(*args, **kwargs)

    def _show_container(self, *args, **kwargs):
        self._ui_manager.show_container(*args, **kwargs)

    def _apply_library_filters(self, *args, **kwargs):
        self._library_manager.apply_library_filters()

    def _run_library_search(self, *args, **kwargs):
        return self._library_manager.run_library_search(*args, **kwargs)

    def _find_repository_item_by_id(self, *args, **kwargs):
        return self._ui_manager.find_repository_item_by_id(*args, **kwargs)

    def _focus_tag_filter_target(self, *args, **kwargs):
        self._ui_manager.focus_tag_filter_target(*args, **kwargs)

    # ------------------------------------------------------------------
    # Container helpers
    # ------------------------------------------------------------------
    def _create_or_update_container(self, *args, **kwargs):
        self._container_manager.create_or_update_container(*args, **kwargs)

    def _handle_container_scan_finished(self, *args, **kwargs):
        self._container_manager.handle_container_scan_finished(*args, **kwargs)

    def _handle_container_scan_error(self, *args, **kwargs):
        self._container_manager.handle_container_scan_error(*args, **kwargs)

    def _add_container_attachments(self, *args, **kwargs):
        self._container_manager.add_container_attachments(*args, **kwargs)

    def _add_container_attachments_from_files(self, *args, **kwargs):
        self._container_manager.add_container_attachments_from_files(*args, **kwargs)

    def _link_container_into_current_container(self, *args, **kwargs):
        self._container_manager.link_container_into_current_container(*args, **kwargs)

    def _import_component_from_linked_container(self, *args, **kwargs):
        self._container_manager.import_component_from_linked_container(*args, **kwargs)

    def _set_primary_component(self, *args, **kwargs):
        self._container_manager.set_primary_component(*args, **kwargs)

    def _handle_container_version_selected(self, *args, **kwargs):
        self._ui_manager.handle_container_version_selected(*args, **kwargs)

    def _create_container_version_snapshot(self, *args, **kwargs):
        self._ui_manager.create_container_version_snapshot(*args, **kwargs)

    def _manage_container_versions(self, *args, **kwargs):
        self._ui_manager.manage_container_versions(*args, **kwargs)

    def _show_tag_graph(self, *args, **kwargs):
        self._ui_manager.show_tag_graph(*args, **kwargs)

    def _close_tag_graph(self, *args, **kwargs):
        self._ui_manager.close_tag_graph(*args, **kwargs)

    def _handle_tag_graph_tag_filter(self, *args, **kwargs):
        self._ui_manager.handle_tag_graph_tag_selected(*args, **kwargs)

    def _open_current_container_folder(self, *args, **kwargs):
        self._container_manager.open_current_container_folder(*args, **kwargs)

    # ------------------------------------------------------------------
    # Container refresh helpers
    # ------------------------------------------------------------------
    def _refresh_current_container(self, *args, **kwargs):
        self._container_manager.refresh_current_container(*args, **kwargs)

    def _watch_container_folder(self, *args, **kwargs):
        self._container_manager.watch_container_folder(*args, **kwargs)

    def _on_fs_changed(self, *args, **kwargs):
        self._container_manager.on_fs_changed(*args, **kwargs)

    def _update_container_watchers(self, *args, **kwargs):
        self._container_manager.update_container_watchers(*args, **kwargs)

    def _open_item_folder(self, *args, **kwargs):
        self._ui_manager.open_item_folder(*args, **kwargs)

    def _navigate_to_path(self, *args, **kwargs):
        self._ui_manager.navigate_to_path(*args, **kwargs)

    def _select_repository_path(self, *args, **kwargs):
        self._ui_manager.select_repository_path(*args, **kwargs)

    def _handle_preview_navigation(self, *args, **kwargs):
        self._ui_manager.handle_preview_navigation(*args, **kwargs)

    def _handle_customization_generated(self, *args, **kwargs):
        self._ui_manager.handle_customization_generated(*args, **kwargs)

    def _rescan_library(self, *args, **kwargs):
        self._library_manager.rescan_library(*args, **kwargs)

    def _new_empty_container_dialog(self, *args, **kwargs):
        self._container_manager.new_empty_container_dialog(*args, **kwargs)

    def _rename_top_level_asset(self, *args, **kwargs):
        self._asset_manager.rename_top_level_asset(*args, **kwargs)

    def _delete_top_level_asset(self, *args, **kwargs):
        self._asset_manager.delete_top_level_asset(*args, **kwargs)

    def _derive_display_name(self, *args, **kwargs):
        return self._asset_manager.derive_display_name(*args, **kwargs)
