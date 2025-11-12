"""Asset management functionality for the 3dfs desktop shell."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import get_config
from ..container import is_container_asset
from ..storage.container_service import ContainerService

if TYPE_CHECKING:
    from PySide6.QtWidgets import QListWidgetItem

    from .main_window import MainWindow


logger = logging.getLogger(__name__)


class AssetManager:
    """Handles asset-related actions for the main window."""

    def __init__(self, main_window: MainWindow) -> None:
        """Initialize the asset manager."""
        self._main_window = main_window

    def derive_display_name(self, asset) -> str:
        metadata = asset.metadata or {}
        display_name = metadata.get("display_name")
        if isinstance(display_name, str) and display_name.strip():
            return display_name.strip()

        label = asset.label if isinstance(asset.label, str) else ""
        if label.startswith("Container:"):
            label = label[len("Container:") :].strip()
        if label:
            return label

        try:
            return Path(asset.path).name
        except Exception:
            return str(asset.path)

    def rename_top_level_asset(self, asset, item: QListWidgetItem | None) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        metadata = dict(asset.metadata or {})

        current_name = self.derive_display_name(asset)

        new_name, accepted = QInputDialog.getText(
            self._main_window,
            "Rename",
            "Display name:",
            text=current_name,
        )
        if not accepted:
            return

        new_name = str(new_name).strip()
        if not new_name or new_name == current_name:
            return

        if any(sep in new_name for sep in (os.sep, "/", "\\")):
            QMessageBox.warning(
                self._main_window,
                "Invalid Name",
                "Display names cannot contain path separators.",
            )
            return

        metadata["display_name"] = new_name
        metadata.pop("container", None)

        label = f"Container: {new_name}"

        try:
            updated_asset = self._main_window._asset_service.update_asset(
                asset.id,
                label=label,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(
                self._main_window,
                "Rename Failed",
                f"Could not update the record:\n{exc}",
            )
            return

        if self._main_window._current_asset and self._main_window._current_asset.id == asset.id:
            self._main_window._current_asset = updated_asset

        self._main_window._container_manager.update_container_watchers()
        self._main_window._ui_manager.select_repository_path(str(updated_asset.path))

        if is_container_asset(updated_asset):
            self._main_window._container_manager.create_or_update_container(
                Path(updated_asset.path),
                select_in_repo=True,
                show_container=True,
                display_name=new_name,
            )
            ContainerService(self._main_window._asset_service).refresh_link_references(updated_asset)
            self._main_window.statusBar().showMessage(
                f"Renamed container to '{new_name}'.",
                4000,
            )
        else:
            self._main_window.statusBar().showMessage(
                f"Renamed item to '{new_name}'.",
                4000,
            )

    def delete_top_level_asset(self, asset, item: QListWidgetItem | None) -> None:
        from PySide6.QtWidgets import QMessageBox

        display_name = self.derive_display_name(asset)

        prompt = f"Delete the container '{display_name}' and all of its files?"

        reply = QMessageBox.question(
            self._main_window,
            "Delete",
            prompt,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        library_root = get_config().library_root
        path = Path(asset.path).expanduser()
        try:
            resolved = path.resolve()
            resolved.relative_to(library_root)
        except Exception:
            QMessageBox.warning(
                self._main_window,
                "Cannot Delete",
                "Only items inside the library root can be deleted from the UI.",
            )
            return

        errors: list[str] = []
        if path.exists():
            try:
                shutil.rmtree(path)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Failed to remove folder: {exc}")

        try:
            self._main_window._asset_service.delete_asset(asset.id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Failed to delete asset record: {exc}")

        if errors:
            QMessageBox.warning(
                self._main_window,
                "Deletion Issues",
                "\n".join(errors),
            )

        if self._main_window._current_asset and self._main_window._current_asset.id == asset.id:
            self._main_window._current_asset = None

        self._main_window._preview_pane.clear()
        self._main_window._detail_stack.setCurrentWidget(self._main_window._preview_pane)
        self._main_window._tag_panel.set_active_item(None)
        self._main_window._container_manager.update_container_watchers()

        self._main_window._library_manager.populate_repository()
        message = f"Deleted container '{display_name}'."
        self._main_window.statusBar().showMessage(message, 4000)
