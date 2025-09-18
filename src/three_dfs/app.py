"""Application bootstrap for the 3dfs desktop shell."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .data import TagStore
from .storage import AssetService
from .ui import PreviewPane, TagSidebar

WINDOW_TITLE: Final[str] = "3dfs"


class MainWindow(QMainWindow):
    """Primary window for the 3dfs shell."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)

        self._asset_service = AssetService()
        self._tag_store = TagStore(service=self._asset_service)
        self._tag_sidebar = TagSidebar(self._tag_store)
        self._repository_list = QListWidget(self)
        self._repository_list.setObjectName("repositoryList")
        self._repository_list.setSelectionMode(QAbstractItemView.SingleSelection)

        self._preview_pane = PreviewPane(
            base_path=Path.cwd(),
            asset_service=self._asset_service,
            parent=self,
        )
        self._preview_pane.setObjectName("previewPane")

        self._build_layout()
        self._connect_signals()
        self._populate_repository()

    # ------------------------------------------------------------------
    # Layout & wiring
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        central_widget = QWidget(self)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal, central_widget)
        splitter.addWidget(self._repository_list)
        splitter.addWidget(self._preview_pane)
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

    def _populate_repository(self) -> None:
        """Populate the repository view with persisted asset entries."""

        self._repository_list.clear()
        assets = self._asset_service.bootstrap_demo_data()

        for asset in assets:
            display_label = asset.label or asset.path
            item = QListWidgetItem(display_label)
            item.setData(Qt.UserRole, asset.path)
            item.setToolTip(asset.path)
            self._repository_list.addItem(item)

        if self._repository_list.count():
            self._repository_list.setCurrentRow(0)

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

        if asset is None:
            self._preview_pane.set_item(
                item_id,
                label=current.text(),
                metadata=None,
                asset_record=None,
            )
        else:
            self._preview_pane.set_item(
                asset.path,
                label=asset.label,
                metadata=asset.metadata,
                asset_record=asset,
            )

        self._tag_sidebar.set_active_item(item_id)

    def _handle_search_request(self, query: str) -> None:
        normalized = query.strip()
        self._apply_search_filter(normalized)

        if normalized:
            message = f"Filtering repository by tag: {normalized}"
        else:
            message = "Cleared tag search"

        self.statusBar().showMessage(message, 2000)

    def _handle_tags_changed(self, item_id: str, tags: list[str]) -> None:
        self._apply_search_filter(self._tag_sidebar.search_text())
        self.statusBar().showMessage(f"{len(tags)} tag(s) assigned to {item_id}", 2000)

    def _apply_search_filter(self, normalized_query: str) -> None:
        normalized = normalized_query.strip()

        if not normalized:
            for row in range(self._repository_list.count()):
                item = self._repository_list.item(row)
                item.setHidden(False)
            return

        matching_items = set(self._tag_store.search(normalized).keys())

        for row in range(self._repository_list.count()):
            item = self._repository_list.item(row)
            item_id = str(item.data(Qt.UserRole) or item.text())
            item.setHidden(item_id not in matching_items)


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
