"""Dialog for adding web links as assets."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

__all__ = ["UrlDialog"]


class UrlDialog(QDialog):
    """Dialog for adding web links as assets with optional screenshot."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Web Link")
        self.setModal(True)

        # URL input
        url_label = QLabel("URL:")
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("https://example.com")
        self._url_input.setMinimumWidth(400)

        # Label/input
        label_label = QLabel("Label:")
        self._label_input = QLineEdit()
        self._label_input.setPlaceholderText("Optional display name")

        # Layout
        form_layout = QFormLayout()
        form_layout.addRow(url_label, self._url_input)
        form_layout.addRow(label_label, self._label_input)

        # Main layout
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Enter web link details:"))
        layout.addLayout(form_layout)

        # Add buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            Qt.Horizontal,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Set focus to URL field
        self._url_input.setFocus()

        self.resize(500, 150)

    def url(self) -> str:
        """Return the entered URL."""
        return self._url_input.text().strip()

    def label(self) -> str:
        """Return the entered label."""
        text = self._label_input.text().strip()
        return text if text else self._url_input.text().strip()
