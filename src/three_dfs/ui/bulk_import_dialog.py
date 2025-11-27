"""Modal dialog for bulk importing existing library files."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

__all__ = ["BulkImportDialog"]


class BulkImportDialog(QDialog):
    """Dialog for bulk importing existing library files."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bulk Import")
        self.setModal(True)

        # Path selection
        self._source_input = QLineEdit()
        self._source_input.setPlaceholderText("Select the folder containing files to import")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._choose_source_directory)

        source_row = QWidget(self)
        source_layout = QHBoxLayout(source_row)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(self._source_input, 1)
        source_layout.addWidget(browse_btn)

        # Options group
        options_box = QGroupBox("Import Options", self)
        options_layout = QFormLayout(options_box)
        options_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        # One container per model option
        self._one_container_per_model = QCheckBox("Create one container per model file")
        self._one_container_per_model.setChecked(True)  # Default behavior
        options_layout.addRow(self._one_container_per_model)

        # Use path for tags option
        self._use_path_for_tags = QCheckBox("Use directory path as tags")
        self._use_path_for_tags.setChecked(True)  # Default behavior
        options_layout.addRow(self._use_path_for_tags)

        # Flatten directory structure option
        self._flatten_structure = QCheckBox("Flatten directory structure (import all files to single container)")
        options_layout.addRow(self._flatten_structure)

        # Include subdirectories option
        self._include_subdirs = QCheckBox("Include subdirectories")
        self._include_subdirs.setChecked(True)  # Default behavior
        options_layout.addRow(self._include_subdirs)

        # File filter options
        filter_box = QGroupBox("File Filters", self)
        filter_layout = QFormLayout(filter_box)
        filter_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._file_extensions_input = QLineEdit()
        self._file_extensions_input.setText(".stl,.obj,.fbx,.gltf,.glb,.step,.stp,.ply,.3mf")
        self._file_extensions_input.setToolTip("Comma-separated list of file extensions to import")
        filter_layout.addRow("File extensions", self._file_extensions_input)

        # Create layout
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select source directory to import from:"))
        layout.addWidget(source_row)
        layout.addWidget(options_box)
        layout.addWidget(filter_box)

        # Add buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            Qt.Horizontal,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resize(600, 400)

    def _choose_source_directory(self) -> None:
        """Open a file dialog to select the source directory."""
        current = self._source_input.text().strip()
        if not current:
            start_dir = str(Path.home())
        else:
            start_dir = current if Path(current).exists() else str(Path.home())

        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select source directory to import from",
            start_dir,
        )
        if chosen:
            self._source_input.setText(chosen)

    def source_directory(self) -> str | None:
        """Return the selected source directory path, or None if not selected."""
        text = self._source_input.text().strip()
        return text if text else None

    def one_container_per_model(self) -> bool:
        """Return whether to create one container per model file."""
        return self._one_container_per_model.isChecked()

    def use_path_for_tags(self) -> bool:
        """Return whether to use directory path as tags."""
        return self._use_path_for_tags.isChecked()

    def flatten_structure(self) -> bool:
        """Return whether to flatten directory structure."""
        return self._flatten_structure.isChecked()

    def include_subdirectories(self) -> bool:
        """Return whether to include subdirectories."""
        return self._include_subdirs.isChecked()

    def file_extensions(self) -> list[str]:
        """Return the list of file extensions to import."""
        extensions_text = self._file_extensions_input.text()
        if not extensions_text.strip():
            return []
        return [ext.strip().lower() for ext in extensions_text.split(",") if ext.strip()]


if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    dialog = BulkImportDialog()
    if dialog.exec() == QDialog.Accepted:
        print(f"Source: {dialog.source_directory()}")
        print(f"One container per model: {dialog.one_container_per_model()}")
        print(f"Use path for tags: {dialog.use_path_for_tags()}")
        print(f"Flatten structure: {dialog.flatten_structure()}")
        print(f"Include subdirs: {dialog.include_subdirectories()}")
        print(f"Extensions: {dialog.file_extensions()}")
    else:
        print("Dialog cancelled")