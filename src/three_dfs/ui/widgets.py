"""Custom Qt widgets for the 3dfs desktop shell."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget


class RepositoryListWidget(QListWidget):
    """A custom QListWidget that handles clicks on the star icon."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._main_window = main_window
        # Add double-click event handling
        self.itemDoubleClicked.connect(self._handle_item_double_clicked)

    def mousePressEvent(self, event):
        """Handle mouse press events."""
        item = self.itemAt(event.pos())
        if item and event.button() == Qt.LeftButton:
            # Check if the click was on the star icon area (right 30 pixels)
            if event.pos().x() > self.width() - 30:
                asset_id = item.data(Qt.UserRole)
                if asset_id is not None:
                    self._main_window._library_manager.toggle_star(asset_id)
                    # We handled the event, so we don't pass it on.
                    # This prevents the item from being selected.
                    return
        # If the click was not on the star, proceed with the default behavior.
        super().mousePressEvent(event)

    def _handle_item_double_clicked(self, item):
        """Handle double-click events on repository items."""
        # Get the asset record to check if it's a URL type
        asset_id = item.data(Qt.UserRole)
        asset_path = item.data(Qt.UserRole + 1) or item.text()

        if asset_id is not None:
            asset = self._main_window._asset_service.get_asset(int(asset_id))
            if asset and isinstance(asset.metadata, dict):
                # Check if this is a URL asset
                asset_kind = asset.metadata.get("kind")
                asset_url = asset.metadata.get("url")

                if asset_kind == "url" and asset_url:
                    # This is a URL asset, open it in the browser
                    self._main_window.open_url_in_browser(asset_url)
                    return

        # For non-URL assets, default to the regular selection behavior
        # This will trigger the currentItemChanged signal and show the preview
