"""Dialog for managing container versions (rename/delete)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..storage import AssetRecord
from ..storage.service import AssetService, ContainerVersionRecord

__all__ = ["VersionManagerDialog"]


class VersionManagerDialog(QDialog):
    """Allow users to rename or delete container versions."""

    versionsChanged = Signal()

    def __init__(
        self,
        asset: AssetRecord,
        service: AssetService,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Versions")
        self.resize(420, 360)
        self._asset = asset
        self._service = service

        self._list = QListWidget(self)
        self._list.setSelectionMode(QListWidget.SingleSelection)

        self._info_label = QLabel("Select a version to rename or delete", self)
        self._info_label.setWordWrap(True)

        self._rename_btn = QPushButton("Rename", self)
        self._rename_btn.clicked.connect(self._handle_rename)
        self._delete_btn = QPushButton("Delete", self)
        self._delete_btn.clicked.connect(self._handle_delete)
        self._close_btn = QPushButton("Close", self)
        self._close_btn.clicked.connect(self.accept)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self._rename_btn)
        button_row.addWidget(self._delete_btn)
        button_row.addWidget(self._close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Container: {asset.label or asset.path}", self))
        layout.addWidget(self._info_label)
        layout.addWidget(self._list, 1)
        layout.addLayout(button_row)

        self._refresh_versions()

    # ------------------------------------------------------------------
    def _refresh_versions(self) -> None:
        self._list.clear()
        versions = self._service.list_container_versions(self._asset.id)
        for record in versions:
            item = QListWidgetItem(self._format_version_label(record))
            item.setData(Qt.UserRole, record)
            self._list.addItem(item)
        has_entries = bool(versions)
        self._rename_btn.setEnabled(has_entries)
        self._delete_btn.setEnabled(has_entries)
        if versions:
            self._list.setCurrentRow(0)

    def _selected_version(self) -> ContainerVersionRecord | None:
        item = self._list.currentItem()
        if item is None:
            return None
        record = item.data(Qt.UserRole)
        return record if isinstance(record, ContainerVersionRecord) else None

    def _handle_rename(self) -> None:
        version = self._selected_version()
        if version is None:
            return
        from PySide6.QtWidgets import QInputDialog

        new_name, accepted = QInputDialog.getText(
            self,
            "Rename Version",
            "New version name:",
            text=version.name,
        )
        if not accepted:
            return
        normalized = new_name.strip()
        if not normalized:
            QMessageBox.warning(self, "Rename Version", "Version name cannot be empty.")
            return
        try:
            self._service.rename_container_version(version.id, name=normalized)
        except ValueError as exc:
            QMessageBox.warning(
                self,
                "Rename Version",
                str(exc) or "Unable to rename version.",
            )
            return
        self._refresh_versions()
        self.versionsChanged.emit()

    def _handle_delete(self) -> None:
        version = self._selected_version()
        if version is None:
            return
        confirm = QMessageBox.question(
            self,
            "Delete Version",
            f"Delete version '{version.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._service.delete_container_version(version.id)
        self._refresh_versions()
        self.versionsChanged.emit()

    @staticmethod
    def _format_version_label(record: ContainerVersionRecord) -> str:
        try:
            timestamp = record.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            timestamp = record.created_at.isoformat()
        return f"{record.name} ({timestamp})"
