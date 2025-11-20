"""Modal dialog that exposes configurable application preferences."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QColorDialog,
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

from ..application.settings import (
    AppSettings,
    DEFAULT_THEME_COLORS,
    DEFAULT_THEME_NAME,
    ThemeColors,
)
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
        self._library_input.setPlaceholderText("Choose the root folder for your library")

        self._custom_themes: dict[str, ThemeColors] = {
            name: palette.copy() for name, palette in settings.custom_themes.items()
        }
        self._theme_colors: ThemeColors = settings.resolved_theme_colors().copy()
        self._theme_combo: QComboBox | None = None
        self._theme_name_input: QLineEdit | None = None
        self._color_buttons: dict[str, QPushButton] = {}

        general_tab = self._build_general_tab(settings)
        interface_tab = self._build_interface_tab(settings)
        containers_tab = self._build_containers_tab(settings)
        appearance_tab = self._build_appearance_tab(settings)

        self._tabs.addTab(general_tab, "General")
        self._tabs.addTab(interface_tab, "Interface")
        self._tabs.addTab(containers_tab, "Containers")
        self._tabs.addTab(appearance_tab, "Appearance")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
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

        self._demo_checkbox = QCheckBox("Seed example entries when the library is empty", library_box)
        self._demo_checkbox.setChecked(settings.bootstrap_demo_data)
        info_label = QLabel(
            "When enabled the application adds a curated set of sample assets " "to help explore features."
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

        self._sidebar_checkbox = QCheckBox("Show repository sidebar on startup", interface_box)
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

    def _build_containers_tab(self, settings: AppSettings) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        containers_box = QGroupBox("Automation")
        containers_layout = QVBoxLayout(containers_box)

        self._auto_refresh_checkbox = QCheckBox(
            "Automatically refresh open containers when files change",
            containers_box,
        )
        self._auto_refresh_checkbox.setChecked(settings.auto_refresh_containers)
        containers_layout.addWidget(self._auto_refresh_checkbox)

        preview_box = QGroupBox("Preview")
        preview_layout = QFormLayout(preview_box)
        preview_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._preview_limit_spin = QSpinBox(preview_box)
        self._preview_limit_spin.setRange(10, 4096)
        self._preview_limit_spin.setSuffix(" KB")
        self._preview_limit_spin.setValue(max(10, settings.text_preview_limit // 1024))
        self._preview_limit_spin.setToolTip("Maximum amount of text loaded when displaying text file previews.")
        preview_layout.addRow("Text preview limit", self._preview_limit_spin)

        layout.addWidget(containers_box)
        layout.addWidget(preview_box)
        layout.addStretch(1)
        return container

    def _build_appearance_tab(self, settings: AppSettings) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        theme_box = QGroupBox("Theme", container)
        theme_layout = QFormLayout(theme_box)
        theme_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self._theme_combo = QComboBox(theme_box)
        self._theme_combo.addItem(DEFAULT_THEME_NAME)
        for name in sorted(self._custom_themes):
            self._theme_combo.addItem(name)
        chosen_theme = settings.theme_name if settings.theme_name in self._custom_themes else DEFAULT_THEME_NAME
        self._theme_combo.setCurrentText(chosen_theme)
        self._theme_combo.currentTextChanged.connect(self._handle_theme_selected)
        theme_layout.addRow("Theme", self._theme_combo)

        save_row = QWidget(theme_box)
        save_row_layout = QHBoxLayout(save_row)
        save_row_layout.setContentsMargins(0, 0, 0, 0)
        save_row_layout.setSpacing(6)
        self._theme_name_input = QLineEdit(save_row)
        self._theme_name_input.setPlaceholderText("Name for saved colorset")
        save_button = QPushButton("Save theme", save_row)
        save_button.clicked.connect(self._save_theme)
        save_row_layout.addWidget(self._theme_name_input, 1)
        save_row_layout.addWidget(save_button)
        theme_layout.addRow("Save as", save_row)

        colors_box = QGroupBox("Colors", container)
        colors_layout = QFormLayout(colors_box)
        colors_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        color_roles = (
            ("window", "Window background"),
            ("panel", "Panel background"),
            ("accent", "Accent / highlight"),
            ("text", "Primary text"),
        )

        for role, label in color_roles:
            button = QPushButton(colors_box)
            button.clicked.connect(lambda _=False, r=role: self._choose_color(r))
            self._color_buttons[role] = button
            colors_layout.addRow(label, button)

        layout.addWidget(theme_box)
        layout.addWidget(colors_box)
        layout.addStretch(1)

        self._refresh_color_buttons()
        return container

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _choose_library_root(self) -> None:
        current = Path(self._library_input.text() or str(self._initial.library_root))
        start_dir = str(current) if current.exists() else str(self._initial.library_root)
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select library root",
            start_dir,
        )
        if chosen:
            self._library_input.setText(chosen)

    def _choose_color(self, role: str) -> None:
        if role not in self._color_buttons:
            return
        current_hex = self._theme_colors.get(role, DEFAULT_THEME_COLORS[role])
        chosen = QColorDialog.getColor(QColor(current_hex), self, f"Select {role} color")
        if not chosen.isValid():
            return
        self._theme_colors[role] = chosen.name()
        self._refresh_color_buttons()

    def _handle_theme_selected(self, name: str) -> None:
        if name == DEFAULT_THEME_NAME:
            self._theme_colors = DEFAULT_THEME_COLORS.copy()
        elif name in self._custom_themes:
            self._theme_colors = self._custom_themes[name].copy()
        else:
            self._theme_colors = DEFAULT_THEME_COLORS.copy()
        self._refresh_color_buttons()

    def _refresh_color_buttons(self) -> None:
        for role, button in self._color_buttons.items():
            value = self._theme_colors.get(role, DEFAULT_THEME_COLORS[role])
            button.setText(value)
            button.setStyleSheet(
                "background-color: %s; color: %s;" % (value, self._text_color_for(value))
            )

    def _save_theme(self) -> None:
        if self._theme_name_input is None or self._theme_combo is None:
            return
        name = self._theme_name_input.text().strip() or self._theme_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "Missing theme name", "Please provide a name for the theme.")
            return
        self._custom_themes[name] = self._theme_colors.copy()
        if self._theme_combo.findText(name) < 0:
            self._theme_combo.addItem(name)
        self._theme_combo.setCurrentText(name)
        self._theme_name_input.clear()

    def _text_color_for(self, background_hex: str) -> str:
        color = QColor(background_hex)
        if not color.isValid():
            return "#000000"
        brightness = (color.red() * 299 + color.green() * 587 + color.blue() * 114) / 1000
        return "#000000" if brightness >= 186 else "#ffffff"

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
            auto_refresh_containers=self._auto_refresh_checkbox.isChecked(),
            bootstrap_demo_data=self._demo_checkbox.isChecked(),
            text_preview_limit=max(10_240, self._preview_limit_spin.value() * 1024),
            theme_name=self._theme_combo.currentText() if self._theme_combo is not None else DEFAULT_THEME_NAME,
            theme_colors=self._theme_colors.copy(),
            custom_themes={name: palette.copy() for name, palette in self._custom_themes.items()},
        )

        self._result = updated
        super().accept()

    def result_settings(self) -> AppSettings | None:
        """Return the settings captured when the dialog was accepted."""

        return self._result
