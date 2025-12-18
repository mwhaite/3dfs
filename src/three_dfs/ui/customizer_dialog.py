"""Dialog wrapper around :class:`CustomizerPanel` for customization runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTabWidget, QVBoxLayout

from ..customizer import CustomizerBackend, ParameterSchema
from ..storage import AssetRecord, AssetService
from .customizer_panel import CustomizerPanel, CustomizerPreviewWidget

__all__ = ["CustomizerDialog", "CustomizerSessionConfig"]


@dataclass(slots=True)
class CustomizerSessionConfig:
    """Describe the state required to launch a customization session."""

    backend: CustomizerBackend
    schema: ParameterSchema
    source_path: Path
    base_asset: AssetRecord
    values: Mapping[str, Any] | None = None
    customization_id: int | None = None


class CustomizerDialog(QDialog):
    """Modal dialog exposing customizer controls for a backend."""

    customizationStarted = Signal()
    customizationSucceeded = Signal(object)
    customizationFailed = Signal(str)

    def __init__(
        self,
        *,
        asset_service: AssetService,
        parent: QDialog | None = None,
    ) -> None:
        super().__init__(parent)
        self._asset_service = asset_service
        self._config: CustomizerSessionConfig | None = None

        self._panel = CustomizerPanel(
            asset_service=self._asset_service,
            parent=self,
        )
        self._preview_widget = CustomizerPreviewWidget(self)
        self._panel.set_preview_widget(self._preview_widget)
        self._panel.customizationStarted.connect(self.customizationStarted)
        self._panel.customizationSucceeded.connect(self._handle_success)
        self._panel.customizationFailed.connect(self.customizationFailed)
        self._panel.previewUpdated.connect(self._activate_preview_tab)

        self._tabs = QTabWidget(self)
        self._parameters_tab_index = self._tabs.addTab(self._panel, "Parameters")
        self._preview_tab_index = self._tabs.addTab(self._preview_widget, "Render")
        self._tabs.setTabEnabled(self._preview_tab_index, False)

        self._button_box = QDialogButtonBox(QDialogButtonBox.Close, self)
        self._button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addWidget(self._tabs)
        layout.addWidget(self._button_box)

        self.setModal(True)
        self.resize(520, 620)

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------
    def set_session(self, config: CustomizerSessionConfig) -> None:
        """Populate the dialog with *config* data."""

        self._config = config
        base_label = config.base_asset.label or Path(config.base_asset.path).name
        self.setWindowTitle(f"Customize {base_label}")
        self._tabs.setTabEnabled(self._preview_tab_index, True)
        self._tabs.setCurrentIndex(self._parameters_tab_index)
        self._panel.set_session(
            backend=config.backend,
            schema=config.schema,
            source_path=config.source_path,
            base_asset=config.base_asset,
            values=config.values,
        )

    def session_config(self) -> CustomizerSessionConfig | None:
        """Return the configuration currently applied to the dialog."""

        return self._config

    def panel(self) -> CustomizerPanel:
        """Expose the underlying :class:`CustomizerPanel` instance."""

        return self._panel

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _handle_success(self, result: object) -> None:
        self.customizationSucceeded.emit(result)

    def _activate_preview_tab(self) -> None:
        self._tabs.setTabEnabled(self._preview_tab_index, True)
        self._tabs.setCurrentIndex(self._preview_tab_index)
