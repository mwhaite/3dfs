"""Custom Qt widgets for the 3dfs desktop shell."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget


class RepositoryListWidget(QListWidget):
    """A custom QListWidget that handles clicks on the star icon."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._main_window = main_window

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
