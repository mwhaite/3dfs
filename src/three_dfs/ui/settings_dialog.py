"""Modal dialog that exposes configurable application preferences."""

from __future__ import annotations

from dataclasses import replace
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
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..application.settings import AppSettings
from ..utils.paths import coerce_required_path

__all__ = ["SettingsDialog"]


class SettingsDialog(QDialog):
    """Collect and persist user-facing preferences."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)

        self._initial = settings
        self._result: AppSettings | None = None

        self._tabs = QTabWidget(self)

        self._library_input = QLineEdit(str(settings.library_root))
        self._library_input.setPlaceholderText(
            "Choose the root folder for your library"
        )

        general_tab = self._build_general_tab(settings)
        interface_tab = self._build_interface_tab(settings)
        projects_tab = self._build_projects_tab(settings)

        self._tabs.addTab(general_tab, "General")
        self._tabs.addTab(interface_tab, "Interface")
        self._tabs.addTab(projects_tab, "Projects")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            Qt.Horizontal,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)
        layout.addWidget(buttons)

        self.resize(520, 360)

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------
    def _build_general_tab(self, settings: AppSettings) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        library_box = QGroupBox("Library")
        library_layout = QFormLayout(library_box)
        library_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        library_path_row = QWidget(library_box)
        library_path_layout = QHBoxLayout(library_path_row)
        library_path_layout.setContentsMargins(0, 0, 0, 0)
        library_path_layout.setSpacing(6)
        library_path_layout.addWidget(self._library_input, 1)
        browse_btn = QPushButton("Browse…", library_path_row)
        browse_btn.clicked.connect(self._choose_library_root)
        library_path_layout.addWidget(browse_btn)
        library_layout.addRow("Library root", library_path_row)

        self._demo_checkbox = QCheckBox(
            "Seed example entries when the library is empty", library_box
        )
        self._demo_checkbox.setChecked(settings.bootstrap_demo_data)
        info_label = QLabel(
            "When enabled the application adds a curated set of sample assets "
            "to help explore features."
        )
        info_label.setWordWrap(True)
        info_label.setObjectName("demoInfoLabel")
        library_layout.addRow(self._demo_checkbox)
        library_layout.addRow(info_label)

        layout.addWidget(library_box)
        layout.addStretch(1)
        return container

    def _build_interface_tab(self, settings: AppSettings) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        interface_box = QGroupBox("Layout")
        interface_layout = QVBoxLayout(interface_box)

        self._sidebar_checkbox = QCheckBox(
            "Show repository sidebar on startup", interface_box
        )
        self._sidebar_checkbox.setChecked(settings.show_repository_sidebar)
        interface_layout.addWidget(self._sidebar_checkbox)

        description = QLabel(
            "The repository sidebar provides quick access to discovered assets. "
            "You can also toggle it from the View menu."
        )
        description.setWordWrap(True)
        interface_layout.addWidget(description)

        layout.addWidget(interface_box)
        layout.addStretch(1)
        return container

    def _build_projects_tab(self, settings: AppSettings) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        projects_box = QGroupBox("Automation")
        projects_layout = QVBoxLayout(projects_box)

        self._auto_refresh_checkbox = QCheckBox(
            "Automatically refresh open projects when files change",
            projects_box,
        )
        self._auto_refresh_checkbox.setChecked(settings.auto_refresh_projects)
        projects_layout.addWidget(self._auto_refresh_checkbox)

        preview_box = QGroupBox("Preview")
        preview_layout = QFormLayout(preview_box)
        preview_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._preview_limit_spin = QSpinBox(preview_box)
        self._preview_limit_spin.setRange(10, 4096)
        self._preview_limit_spin.setSuffix(" KB")
        self._preview_limit_spin.setValue(
            max(10, settings.text_preview_limit // 1024)
        )
        self._preview_limit_spin.setToolTip(
            "Maximum amount of text loaded when displaying README or source files."
        )
        preview_layout.addRow("Text preview limit", self._preview_limit_spin)

        layout.addWidget(projects_box)
        layout.addWidget(preview_box)
        layout.addStretch(1)
        return container

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _choose_library_root(self) -> None:
        current = Path(self._library_input.text() or str(self._initial.library_root))
        start_dir = (
            str(current) if current.exists() else str(self._initial.library_root)
        )
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select library root",
            start_dir,
        )
        if chosen:
            self._library_input.setText(chosen)

    def accept(self) -> None:  # type: ignore[override]
        try:
            library_root = coerce_required_path(self._library_input.text())
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(
                self,
                "Invalid library path",
                str(exc),
            )
            return

        updated = replace(
            self._initial,
            library_root=library_root,
            show_repository_sidebar=self._sidebar_checkbox.isChecked(),
            auto_refresh_projects=self._auto_refresh_checkbox.isChecked(),
            bootstrap_demo_data=self._demo_checkbox.isChecked(),
            text_preview_limit=max(10_240, self._preview_limit_spin.value() * 1024),
        )

        self._result = updated
        super().accept()

    def result_settings(self) -> AppSettings | None:
        """Return the settings captured when the dialog was accepted."""

        return self._result
