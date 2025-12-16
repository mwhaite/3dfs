"""Custom Qt widgets for the 3dfs desktop shell."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget


class RepositoryListWidget(QListWidget):
    """A custom QListWidget that handles clicks on the star icon."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._main_window = main_window
        # Enable drag and drop
        self.setAcceptDrops(True)
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

    def dragEnterEvent(self, event):
        """Handle drag enter events - accept folders and files."""
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            # Check if any of the URLs are directories
            for url in mime_data.urls():
                import os
                if url.isLocalFile() and os.path.isdir(url.toLocalFile()):
                    event.acceptProposedAction()
                    return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        """Handle drag move events."""
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            # Check if any of the URLs are directories
            for url in mime_data.urls():
                import os
                if url.isLocalFile() and os.path.isdir(url.toLocalFile()):
                    event.acceptProposedAction()
                    return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        """Handle drop events - import folders as containers."""
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            import os
            import shutil
            from pathlib import Path
            from ..config import get_config

            # Get the library root to copy files to
            library_root = get_config().library_root

            # Process each dropped URL
            for url in mime_data.urls():
                if url.isLocalFile():
                    folder_path = url.toLocalFile()
                    if os.path.isdir(folder_path):
                        # Import the folder as a container
                        folder_name = os.path.basename(folder_path.rstrip('/\\'))

                        # Create a new container folder in the library
                        container_path = library_root / folder_name

                        # Handle duplicate names
                        counter = 1
                        original_container_path = container_path
                        while container_path.exists():
                            container_path = original_container_path.parent / f"{original_container_path.name}_{counter}"
                            counter += 1
                            if counter > 100:  # Prevent infinite loop
                                break

                        try:
                            # Copy the entire folder to the library
                            shutil.copytree(folder_path, container_path)

                            # Create or update the container in the asset service
                            self._main_window._container_manager.create_or_update_container(
                                container_path,
                                select_in_repo=True,
                                show_container=True
                            )

                            self._main_window.statusBar().showMessage(
                                f"Imported folder '{folder_name}' as container",
                                4000
                            )

                        except Exception as e:
                            self._main_window.statusBar().showMessage(
                                f"Failed to import folder: {str(e)}",
                                4000
                            )

            event.acceptProposedAction()
            return

        super().dropEvent(event)
