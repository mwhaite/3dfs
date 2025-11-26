"""Menu management functionality for the 3dfs desktop shell."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenuBar

from ..config import get_config

if TYPE_CHECKING:
    from .main_window import MainWindow


logger = logging.getLogger(__name__)


class MenuManager:
    """Handles menu-related actions for the main window."""

    def __init__(self, main_window: MainWindow) -> None:
        """Initialize the menu manager."""
        self._main_window = main_window

    def build_menu(self) -> None:
        menubar = self._main_window.menuBar()
        if menubar is None:
            menubar = QMenuBar(self._main_window)
            self._main_window.setMenuBar(menubar)

        file_menu = menubar.addMenu("&File")

        rescan_action = QAction("Rescan Library", self._main_window)
        rescan_action.setShortcut("F5")
        rescan_action.triggered.connect(self._main_window._library_manager.rescan_library)
        file_menu.addAction(rescan_action)

        file_menu.addSeparator()

        settings_action = QAction("Settings…", self._main_window)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._main_window._open_settings_dialog)
        file_menu.addAction(settings_action)

        bulk_import_action = QAction("Bulk Import…", self._main_window)
        bulk_import_action.triggered.connect(self._main_window._open_bulk_import_dialog)
        file_menu.addAction(bulk_import_action)

        add_url_action = QAction("Add Web Link…", self._main_window)
        add_url_action.triggered.connect(self._main_window.add_web_link)
        file_menu.addAction(add_url_action)

        # Container actions
        container_menu = menubar.addMenu("&Containers")
        from PySide6.QtWidgets import QFileDialog

        def _new_container_from_folder() -> None:
            config = get_config()
            folder = QFileDialog.getExistingDirectory(
                self._main_window,
                "Choose container folder",
                str(config.library_root),
            )
            if not folder:
                return
            self._main_window._container_manager.create_or_update_container(Path(folder))

        new_container_action = QAction("New Container From Folder…", self._main_window)
        new_container_action.triggered.connect(_new_container_from_folder)
        container_menu.addAction(new_container_action)

        new_empty_container_action = QAction("New Empty Container…", self._main_window)
        new_empty_container_action.triggered.connect(self._main_window._container_manager.new_empty_container_dialog)
        container_menu.addAction(new_empty_container_action)

        add_attachment_action = QAction("Upload File(s) to Current Container…", self._main_window)
        add_attachment_action.triggered.connect(self._main_window._container_manager.add_container_attachments)
        container_menu.addAction(add_attachment_action)

        import_url_action = QAction("Import from URL…", self._main_window)
        import_url_action.triggered.connect(self._main_window._import_from_url)
        container_menu.addAction(import_url_action)

        sidebar_menu = menubar.addMenu("&View")
        toggle_repo_action = QAction("Toggle Repository Sidebar", self._main_window)
        toggle_repo_action.setShortcut("Ctrl+R")
        toggle_repo_action.setCheckable(True)
        toggle_repo_action.toggled.connect(self._main_window._ui_manager.toggle_repository_sidebar)
        sidebar_menu.addAction(toggle_repo_action)
        toggle_repo_action.setChecked(self._main_window._settings.show_repository_sidebar)
        self._main_window._toggle_repo_action = toggle_repo_action

        toggle_tag_action = QAction("Show Tag Panel", self._main_window)
        toggle_tag_action.setShortcut("Ctrl+T")
        toggle_tag_action.setCheckable(True)
        toggle_tag_action.toggled.connect(self._main_window._ui_manager.toggle_tag_panel)
        sidebar_menu.addAction(toggle_tag_action)
        toggle_tag_action.setChecked(True)
        self._main_window._tag_panel_action = toggle_tag_action

    def show_repo_context_menu(self, pos) -> None:
        current_item = self._main_window._repository_list.currentItem()
        try:
            clicked_item = self._main_window._repository_list.itemAt(pos)
        except Exception:
            clicked_item = None
        item = clicked_item or current_item
        if clicked_item is not None and clicked_item is not current_item:
            try:
                self._main_window._repository_list.setCurrentItem(clicked_item)
            except Exception:
                pass
            item = clicked_item
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self._main_window)
        customize_act = None
        if item is not None and self._main_window._preview_pane.can_customize:
            customize_act = menu.addAction("Customize…")
            menu.addSeparator()
        new_container_act = menu.addAction("New Empty Container…")
        open_container_act = menu.addAction("Open as Container")
        open_folder_act = menu.addAction("Open Containing Folder")

        asset = None
        asset_id_value = item.data(Qt.UserRole) if item is not None else None
        if asset_id_value is not None:
            try:
                asset = self._main_window._asset_service.get_asset(int(asset_id_value))
            except Exception:
                asset = None

        rename_act = None
        delete_act = None
        asset_path: Path | None = None
        can_modify = False
        container_type = None
        if asset is not None:
            asset_path = Path(asset.path).expanduser()
            metadata = asset.metadata or {}
            container_type = metadata.get("container_type")
            if not isinstance(container_type, str) or not container_type:
                container_type = "container"
            library_root = get_config().library_root
            try:
                resolved = asset_path.resolve()
            except Exception:
                resolved = asset_path
            try:
                resolved.relative_to(library_root)
            except Exception:
                can_modify = False
            else:
                can_modify = asset_path.is_dir()

        has_selection = item is not None
        open_container_act.setEnabled(has_selection)
        open_folder_act.setEnabled(has_selection)

        if can_modify and asset is not None:
            display_label = metadata.get("display_name") if isinstance(metadata, dict) else None
            if not isinstance(display_label, str) or not display_label.strip():
                display_label = self._main_window._asset_manager.derive_display_name(asset)
            rename_act = menu.addAction(f"Rename '{display_label}'…")
            delete_act = menu.addAction(f"Delete '{display_label}'…")
            menu.addSeparator()

        global_pos = self._main_window._repository_list.mapToGlobal(pos)
        action = menu.exec(global_pos)
        if action is None:
            return
        if action == customize_act:
            self._main_window._preview_pane.launch_customizer()
            return
        if action == new_container_act:
            self._main_window._container_manager.new_empty_container_dialog()
            return
        if action == open_container_act:
            target = None
            if item is not None:
                raw_path = item.data(Qt.UserRole + 1) or item.text()
                target = str(raw_path) if raw_path is not None else ""
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
                    self._main_window.statusBar().showMessage(
                        "Folder is outside the library root.",
                        3000,
                    )
                    return
                self._main_window._container_manager.create_or_update_container(
                    folder,
                    select_in_repo=True,
                    show_container=True,
                )
                for row in range(self._main_window._repository_list.count()):
                    candidate = self._main_window._repository_list.item(row)
                    candidate_raw = candidate.data(Qt.UserRole + 1) or candidate.text()
                    candidate_path = str(candidate_raw) if candidate_raw is not None else ""
                    if candidate_path == str(folder):
                        self._main_window._repository_list.setCurrentItem(candidate)
                        break
        elif action == open_folder_act:
            target = None
            if item is not None:
                raw_path = item.data(Qt.UserRole + 1) or item.text()
                target = str(raw_path) if raw_path is not None else ""
            if target:
                self._main_window._ui_manager.open_item_folder(target)
        elif action == rename_act and asset is not None and asset_path is not None:
            self._main_window._rename_top_level_asset(asset, item)
        elif action == delete_act and asset is not None and asset_path is not None:
            self._main_window._delete_top_level_asset(asset, item)
