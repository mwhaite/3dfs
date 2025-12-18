"""UI helpers for interacting with customizer backends."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ..customizer import (
    CustomizerBackend,
    CustomizerSession,
    ParameterDescriptor,
    ParameterSchema,
    execute_customization,
)
from ..storage import AssetRecord, AssetService
from .model_viewer import ModelViewer, _MeshData, load_mesh_data

__all__ = [
    "BooleanParameterWidget",
    "ChoiceParameterWidget",
    "CustomizerPreviewWidget",
    "CustomizerPanel",
    "NumberParameterWidget",
    "RangeParameterWidget",
    "StringParameterWidget",
]


logger = logging.getLogger(__name__)


class CustomizerPreviewWidget(QWidget):
    """Display area for customizer render previews."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._title = QLabel("Render", self)
        self._title.setObjectName("customizerPreviewTitle")
        layout.addWidget(self._title)

        self._stack_widget = QWidget(self)
        self._stack = QStackedLayout(self._stack_widget)
        self._message_label = QLabel("Render the customised model to view it here.", self._stack_widget)
        self._message_label.setAlignment(Qt.AlignCenter)
        self._message_label.setWordWrap(True)
        self._stack.addWidget(self._message_label)

        self._viewer = ModelViewer(self._stack_widget)
        self._viewer.setMinimumHeight(200)
        self._stack.addWidget(self._viewer)
        layout.addWidget(self._stack_widget, 1)

        self._preview_mesh: _MeshData | None = None
        self._preview_parameters: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Preview state helpers
    # ------------------------------------------------------------------
    def clear(self) -> None:
        """Reset the preview content to its default message."""

        self._preview_mesh = None
        self._preview_parameters = None
        self._viewer.clear()
        self._message_label.setText("Render the customised model to view it here.")
        self._stack.setCurrentWidget(self._message_label)

    def mark_parameters_changed(self) -> None:
        """Inform the widget that parameters changed and preview is stale."""

        if self._preview_mesh is not None:
            self._message_label.setText("Parameters changed. Render again to update the model.")
        else:
            self._message_label.setText("Render the customised model to view it here.")
        self._preview_mesh = None
        self._preview_parameters = None
        self._stack.setCurrentWidget(self._message_label)

    def show_message(self, message: str) -> None:
        """Display *message* instead of a mesh preview."""

        self._message_label.setText(message)
        self._stack.setCurrentWidget(self._message_label)

    def set_preview(self, mesh: _MeshData, mesh_path: Path, parameters: Mapping[str, Any]) -> None:
        """Show the rendered *mesh* and persist the applied *parameters*."""

        self._preview_mesh = mesh
        self._preview_parameters = dict(parameters)
        self._viewer.set_mesh_data(mesh, mesh_path)
        self._stack.setCurrentWidget(self._viewer)

    def has_preview(self) -> bool:
        """Return ``True`` when a mesh preview is active."""

        return self._preview_mesh is not None

    def preview_parameters(self) -> dict[str, Any] | None:
        """Return the parameter values that produced the current preview."""

        if self._preview_parameters is None:
            return None
        return dict(self._preview_parameters)


def _is_integer_descriptor(descriptor: ParameterDescriptor) -> bool:
    value = descriptor.default
    return isinstance(value, int) and not isinstance(value, bool)


def _infer_decimals(step: float | None) -> int:
    if step is None:
        return 6
    text = (f"{step:.12f}").rstrip("0").rstrip(".")
    if "." not in text:
        return 0
    return max(0, len(text.split(".")[1]))


class ParameterWidget(QWidget):
    """Base class for widgets representing customizer parameters."""

    valueChanged = Signal(object)

    def __init__(self, descriptor: ParameterDescriptor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._descriptor = descriptor

    @property
    def descriptor(self) -> ParameterDescriptor:
        return self._descriptor

    @property
    def name(self) -> str:
        return self._descriptor.name

    def value(self) -> Any:  # pragma: no cover - interface definition
        raise NotImplementedError

    def set_value(self, value: Any) -> None:  # pragma: no cover - interface definition
        raise NotImplementedError

    def reset(self) -> None:
        self.set_value(self._descriptor.default)


class BooleanParameterWidget(ParameterWidget):
    """Checkbox-based editor for boolean parameters."""

    def __init__(self, descriptor: ParameterDescriptor, parent: QWidget | None = None) -> None:
        super().__init__(descriptor, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._checkbox = QCheckBox(self)
        self._checkbox.setChecked(bool(descriptor.default))
        self._checkbox.toggled.connect(self._emit_value)
        layout.addWidget(self._checkbox)
        layout.addStretch(1)

    def _emit_value(self, _: bool) -> None:
        self.valueChanged.emit(self.value())

    def value(self) -> bool:
        return self._checkbox.isChecked()

    def set_value(self, value: Any) -> None:
        self._checkbox.setChecked(bool(value))


class NumberParameterWidget(ParameterWidget):
    """Spin-box based editor for numeric parameters."""

    def __init__(self, descriptor: ParameterDescriptor, parent: QWidget | None = None) -> None:
        super().__init__(descriptor, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._is_integer = _is_integer_descriptor(descriptor)
        minimum = descriptor.minimum
        maximum = descriptor.maximum
        step = descriptor.step

        if self._is_integer:
            self._spin: QSpinBox | QDoubleSpinBox = QSpinBox(self)
            spin: QSpinBox = self._spin  # type: ignore[assignment]
            min_value = int(minimum) if minimum is not None else -1_000_000
            max_value = int(maximum) if maximum is not None else 1_000_000
            spin.setRange(min_value, max_value)
            spin.setSingleStep(int(step) if step is not None else 1)
            spin.setValue(int(descriptor.default))
        else:
            self._spin = QDoubleSpinBox(self)
            double_spin: QDoubleSpinBox = self._spin  # type: ignore[assignment]
            min_value = float(minimum) if minimum is not None else -1_000_000.0
            max_value = float(maximum) if maximum is not None else 1_000_000.0
            double_spin.setRange(min_value, max_value)
            single_step = float(step) if step is not None else 0.1
            double_spin.setSingleStep(single_step)
            double_spin.setDecimals(_infer_decimals(step))
            double_spin.setValue(float(descriptor.default))

        self._spin.valueChanged.connect(self._emit_value)
        layout.addWidget(self._spin)
        layout.addStretch(1)

    def _emit_value(self) -> None:
        self.valueChanged.emit(self.value())

    @property
    def minimum(self) -> float | int | None:
        return self.descriptor.minimum

    @property
    def maximum(self) -> float | int | None:
        return self.descriptor.maximum

    @property
    def step(self) -> float | int | None:
        return self.descriptor.step

    def value(self) -> int | float:
        raw = self._spin.value()
        return int(raw) if self._is_integer else float(raw)

    def set_value(self, value: Any) -> None:
        if self._is_integer:
            coerced = int(value)
        else:
            coerced = float(value)
        minimum = self.minimum
        maximum = self.maximum
        if minimum is not None and coerced < minimum:
            coerced = minimum
        if maximum is not None and coerced > maximum:
            coerced = maximum
        self._spin.blockSignals(True)
        self._spin.setValue(coerced)
        self._spin.blockSignals(False)
        self.valueChanged.emit(self.value())


class RangeParameterWidget(ParameterWidget):
    """Slider + spin box editor for numeric ranges."""

    def __init__(self, descriptor: ParameterDescriptor, parent: QWidget | None = None) -> None:
        super().__init__(descriptor, parent)

        self._is_integer = _is_integer_descriptor(descriptor)
        minimum = descriptor.minimum if descriptor.minimum is not None else descriptor.default
        maximum = descriptor.maximum if descriptor.maximum is not None else descriptor.default
        step = descriptor.step
        if step is None:
            step = 1 if self._is_integer else 0.1
        self._minimum = float(minimum)
        self._maximum = float(maximum)
        self._step = float(step)
        if self._maximum < self._minimum:
            self._maximum = self._minimum

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        span = max(0.0, self._maximum - self._minimum)
        slider_steps = int(round(span / self._step)) if self._step else 0
        self._slider = QSlider(Qt.Horizontal, self)
        self._slider.setRange(0, max(0, slider_steps))
        self._slider.setSingleStep(1)

        if self._is_integer:
            self._spin: QSpinBox | QDoubleSpinBox = QSpinBox(self)
            spin: QSpinBox = self._spin  # type: ignore[assignment]
            spin.setRange(int(round(self._minimum)), int(round(self._maximum)))
            spin.setSingleStep(int(round(self._step)))
        else:
            self._spin = QDoubleSpinBox(self)
            double_spin: QDoubleSpinBox = self._spin  # type: ignore[assignment]
            double_spin.setRange(self._minimum, self._maximum)
            double_spin.setSingleStep(self._step)
            double_spin.setDecimals(_infer_decimals(self._step))

        self._spin.valueChanged.connect(self._sync_from_spin)
        self._slider.valueChanged.connect(self._sync_from_slider)

        layout.addWidget(self._slider)
        self._spin.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        layout.addWidget(self._spin)

        self.set_value(descriptor.default)

    @property
    def minimum(self) -> float:
        return self._minimum

    @property
    def maximum(self) -> float:
        return self._maximum

    @property
    def step(self) -> float:
        return self._step

    def _sync_from_slider(self, position: int) -> None:
        value = self._minimum + position * self._step
        if value > self._maximum:
            value = self._maximum
        if self._is_integer:
            value = int(round(value))
        self._spin.blockSignals(True)
        self._spin.setValue(value)
        self._spin.blockSignals(False)
        self.valueChanged.emit(self.value())

    def _sync_from_spin(self, value: float) -> None:
        normalized = (value - self._minimum) / self._step if self._step else 0
        position = int(round(normalized))
        position = max(self._slider.minimum(), min(self._slider.maximum(), position))
        self._slider.blockSignals(True)
        self._slider.setValue(position)
        self._slider.blockSignals(False)
        self.valueChanged.emit(self.value())

    def value(self) -> int | float:
        raw = self._spin.value()
        return int(raw) if self._is_integer else float(raw)

    def set_value(self, value: Any) -> None:
        coerced = int(value) if self._is_integer else float(value)
        if coerced < self._minimum:
            coerced = self._minimum
        if coerced > self._maximum:
            coerced = self._maximum
        position = int(round((coerced - self._minimum) / self._step)) if self._step else 0
        position = max(self._slider.minimum(), min(self._slider.maximum(), position))
        self._slider.blockSignals(True)
        self._slider.setValue(position)
        self._slider.blockSignals(False)
        self._spin.blockSignals(True)
        self._spin.setValue(coerced)
        self._spin.blockSignals(False)
        self.valueChanged.emit(self.value())


class ChoiceParameterWidget(ParameterWidget):
    """Drop-down based editor for enumerated parameters."""

    def __init__(self, descriptor: ParameterDescriptor, parent: QWidget | None = None) -> None:
        super().__init__(descriptor, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._combo = QComboBox(self)
        for choice in descriptor.choices:
            label = str(choice)
            self._combo.addItem(label, userData=choice)
        self._combo.currentIndexChanged.connect(self._emit_value)
        layout.addWidget(self._combo)
        layout.addStretch(1)

        self.set_value(descriptor.default)

    def _emit_value(self, _: int) -> None:
        self.valueChanged.emit(self.value())

    def options(self) -> tuple[Any, ...]:
        return tuple(self.descriptor.choices)

    def value(self) -> Any:
        return self._combo.currentData()

    def set_value(self, value: Any) -> None:
        for index in range(self._combo.count()):
            if self._combo.itemData(index) == value:
                self._combo.blockSignals(True)
                self._combo.setCurrentIndex(index)
                self._combo.blockSignals(False)
                self.valueChanged.emit(self.value())
                return
        if self._combo.count():
            self._combo.blockSignals(True)
            self._combo.setCurrentIndex(0)
            self._combo.blockSignals(False)
            self.valueChanged.emit(self.value())


class StringParameterWidget(ParameterWidget):
    """Line edit for string based parameters."""

    def __init__(self, descriptor: ParameterDescriptor, parent: QWidget | None = None) -> None:
        super().__init__(descriptor, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._line_edit = QLineEdit(self)
        self._line_edit.setText(str(descriptor.default))
        self._line_edit.textChanged.connect(self._emit_value)
        layout.addWidget(self._line_edit)

    def _emit_value(self, _: str) -> None:
        self.valueChanged.emit(self.value())

    def value(self) -> str:
        return self._line_edit.text()

    def set_value(self, value: Any) -> None:
        self._line_edit.blockSignals(True)
        self._line_edit.setText(str(value))
        self._line_edit.blockSignals(False)
        self.valueChanged.emit(self.value())


def _create_parameter_widget(descriptor: ParameterDescriptor) -> ParameterWidget:
    kind = descriptor.kind.lower()
    if kind == "boolean":
        return BooleanParameterWidget(descriptor)
    if kind == "choice":
        return ChoiceParameterWidget(descriptor)
    if kind == "range":
        return RangeParameterWidget(descriptor)
    if kind in {"number", "numeric"}:
        return NumberParameterWidget(descriptor)
    return StringParameterWidget(descriptor)


@dataclass(slots=True)
class _SessionContext:
    backend: CustomizerBackend
    schema: ParameterSchema
    source_path: Path
    base_asset: AssetRecord | None
    derivative_path: Path | None = None
    customization_id: int | None = None


class CustomizerPanel(QWidget):
    """Container widget exposing sliders and toggles for a schema."""

    customizationStarted = Signal()
    customizationSucceeded = Signal(object)
    customizationFailed = Signal(str)
    previewUpdated = Signal()

    def __init__(
        self,
        *,
        asset_service: AssetService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._asset_service = asset_service
        self._session: _SessionContext | None = None
        self._editors: dict[str, ParameterWidget] = {}
        self._preview_widget: CustomizerPreviewWidget | None = None

        self._intro_label = QLabel("Adjust the parameters below to customise this design.", self)
        self._intro_label.setWordWrap(True)

        self._form_layout = QFormLayout()
        self._form_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self._form_layout.setContentsMargins(0, 0, 0, 0)
        self._form_layout.setSpacing(8)

        self._form_container = QWidget(self)
        self._form_container.setLayout(self._form_layout)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._form_container)

        self._status_label = QLabel("", self)
        self._status_label.setObjectName("customizerStatus")
        self._status_label.setWordWrap(True)

        self._reset_button = QPushButton("Reset", self)
        self._reset_button.clicked.connect(self.reset_parameters)
        self._preview_button = QPushButton("Render", self)
        self._preview_button.clicked.connect(self._handle_preview)
        self._save_button = QPushButton("Save Model", self)
        self._save_button.clicked.connect(self._handle_generate)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        button_row.addStretch(1)
        button_row.addWidget(self._reset_button)
        button_row.addWidget(self._preview_button)
        button_row.addWidget(self._save_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._intro_label)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(self._status_label)
        layout.addLayout(button_row)

        self.clear()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def clear(self) -> None:
        """Remove all parameter editors and reset the panel state."""

        while self._form_layout.rowCount():
            self._form_layout.removeRow(0)
        self._editors.clear()
        self._session = None
        self._status_label.clear()
        if self._preview_widget is not None:
            self._preview_widget.clear()
        self._update_action_states()

    def set_preview_widget(self, widget: CustomizerPreviewWidget | None) -> None:
        """Attach *widget* so previews can be rendered outside the panel."""

        if self._preview_widget is widget:
            return
        if self._preview_widget is not None:
            self._preview_widget.clear()
        self._preview_widget = widget
        if self._preview_widget is not None:
            self._preview_widget.clear()
        self._update_action_states()

    def set_session(
        self,
        *,
        backend: CustomizerBackend,
        schema: ParameterSchema,
        source_path: str | Path,
        base_asset: AssetRecord | None = None,
        values: Mapping[str, Any] | None = None,
        derivative_path: Path | None = None,
        customization_id: int | None = None,
    ) -> None:
        """Populate the panel using *schema* from *backend*."""

        self.clear()
        source = Path(source_path)
        self._session = _SessionContext(
            backend=backend,
            schema=schema,
            source_path=source,
            base_asset=base_asset,
            derivative_path=derivative_path,
            customization_id=customization_id,
        )

        if self._session.derivative_path:
            self._save_button.setText("Update Model")
        elif source.name.endswith("_customized.scad"):
            self._save_button.setText("Update STL")
        else:
            self._save_button.setText("Save Model")

        normalized_values: Mapping[str, Any] = values or {}
        try:
            normalized_values = backend.validate(schema, normalized_values)
        except Exception:
            normalized_values = values or {}

        for descriptor in schema.parameters:
            widget = _create_parameter_widget(descriptor)
            if descriptor.description:
                widget.setToolTip(str(descriptor.description))
            try:
                initial = normalized_values.get(descriptor.name, descriptor.default)
            except AttributeError:
                initial = descriptor.default
            widget.set_value(initial)
            widget.valueChanged.connect(self._handle_value_changed)

            label = QLabel(descriptor.name, self)
            if descriptor.description:
                label.setToolTip(str(descriptor.description))
            self._form_layout.addRow(label, widget)
            self._editors[descriptor.name] = widget

        has_parameters = bool(self._editors)
        if not has_parameters:
            self._status_label.setText("No customizable parameters found in this file.")
        elif not self._can_save():
            self._status_label.setText("Register this container in the library to generate customized builds.")
        else:
            self._status_label.clear()

        self._update_action_states()

    def _update_action_states(self) -> None:
        logger.info("CustomizerPanel._update_action_states()")
        has_parameters = bool(self._editors)
        preview_available = self._session is not None and has_parameters and self._preview_widget is not None
        self._reset_button.setEnabled(has_parameters)
        self._preview_button.setEnabled(preview_available)
        self._save_button.setEnabled(self._can_save())

    def _can_save(self) -> bool:
        return (
            bool(self._editors)
            and self._session is not None
            and self._session.base_asset is not None
            and self._asset_service is not None
        )

    def parameter_names(self) -> tuple[str, ...]:
        """Return the names of currently active parameters."""

        return tuple(self._editors)

    def editor(self, name: str) -> ParameterWidget:
        """Return the editor widget associated with *name*."""

        return self._editors[name]

    def parameter_values(self) -> dict[str, Any]:
        """Return the current parameter values collected from the editors."""

        return {name: widget.value() for name, widget in self._editors.items()}

    def reset_parameters(self) -> None:
        """Restore all parameters to their default values."""

        for widget in self._editors.values():
            widget.reset()
        self._status_label.clear()
        self._invalidate_preview()
        self._update_action_states()

    @property
    def can_execute(self) -> bool:
        """Return ``True`` when the Generate action is available."""

        return self._save_button.isEnabled()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _handle_value_changed(self, _value: object) -> None:
        del _value
        if self._status_label.text():
            self._status_label.clear()
        self._invalidate_preview()
        self._update_action_states()

    def _invalidate_preview(self) -> None:
        if self._preview_widget is not None:
            self._preview_widget.mark_parameters_changed()

    def _handle_preview(self) -> None:
        context = self._session
        if context is None:
            self._status_label.setText("Customizer session is not initialised.")
            return
        if self._preview_widget is None:
            self._status_label.setText("Preview is unavailable in this context.")
            return

        backend = context.backend
        schema = context.schema
        source_path = context.source_path
        try:
            normalized = backend.validate(schema, self.parameter_values())
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or "Invalid parameter values supplied."
            self._status_label.setText(message)
            return

        self._status_label.setText("Rendering model…")
        self._preview_button.setEnabled(False)
        self._save_button.setEnabled(False)
        try:
            with tempfile.TemporaryDirectory(prefix="three_dfs_preview_") as tmp:
                work_dir = Path(tmp)
                session = backend.plan_build(
                    source_path,
                    schema,
                    normalized,
                    output_dir=work_dir,
                    execute=True,
                    metadata={"intent": "preview"},
                )
                mesh_path = self._select_mesh_artifact(session)
                if mesh_path is None or not mesh_path.exists():
                    raise FileNotFoundError("Preview model was not produced by the customizer.")
                mesh, error = load_mesh_data(mesh_path)
                if mesh is None:
                    raise RuntimeError(error or "Preview mesh could not be loaded.")
                self._preview_widget.set_preview(mesh, mesh_path, normalized)
                self.previewUpdated.emit()
                self._status_label.setText("Preview updated with current parameters.")
        except FileNotFoundError as exc:
            message = str(exc) or ("OpenSCAD executable not found. Ensure it is installed and on PATH.")
            self._preview_widget.show_message(message)
            self._status_label.setText(message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to render customizer preview")
            message = str(exc) or "Preview failed."
            self._preview_widget.show_message(message)
            self._status_label.setText(message)
        finally:
            self._update_action_states()

    def _compose_container_name(self, base_asset: AssetRecord, values: Mapping[str, Any]) -> str:
        base_name = base_asset.label or "custom"
        summary = self._summarize_parameters(values)
        if summary:
            return f"{base_name}_{summary}"
        return base_name

    def _prompt_container_name(self, default_name: str) -> str | None:
        while True:
            name, accepted = QInputDialog.getText(
                self,
                "Save Customized Container",
                "Container name:",
                text=default_name,
            )
            if not accepted:
                return None
            trimmed = str(name).strip()
            if trimmed:
                return trimmed
            QMessageBox.warning(
                self,
                "Invalid Container Name",
                "Container name cannot be empty.",
            )

    def _summarize_parameters(self, values: Mapping[str, Any], *, limit: int = 3) -> str:
        if not values:
            return ""
        pieces: list[str] = []
        for index, name in enumerate(sorted(values)):
            formatted = self._format_parameter_value(values[name])
            pieces.append(f"{name}={formatted}")
            if index + 1 >= limit:
                break
        return ", ".join(pieces)

    def _format_parameter_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float):
            text = f"{value:.4f}"
            text = text.rstrip("0").rstrip(".")
            return text or "0"
        return str(value)

    def _handle_update_model(self) -> None:
        if self._session is None or self._session.derivative_path is None:
            self._status_label.setText("Customizer session is not initialised for update.")
            return

        import shutil

        backend = self._session.backend
        schema = self._session.schema
        try:
            normalized = backend.validate(schema, self.parameter_values())
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or "Invalid parameter values supplied."
            self._status_label.setText(message)
            return

        stl_path = self._session.derivative_path

        self._status_label.setText("Updating model…")
        self._preview_button.setEnabled(False)
        self._save_button.setEnabled(False)
        try:
            with tempfile.TemporaryDirectory(prefix="three_dfs_update_") as tmp:
                work_dir = Path(tmp)
                session = backend.plan_build(
                    self._session.source_path,
                    schema,
                    normalized,
                    output_dir=work_dir,
                    execute=True,
                    metadata={"intent": "update"},
                )
                mesh_path = self._select_mesh_artifact(session)
                if mesh_path is None or not mesh_path.exists():
                    raise FileNotFoundError("Updated model was not produced by the customizer.")

                shutil.copy2(mesh_path, stl_path)

                if self._asset_service:
                    if self._session.customization_id is not None:
                        self._asset_service.update_customization(
                            self._session.customization_id,
                            parameter_values=normalized,
                        )

                    asset_record = self._asset_service.ensure_asset(str(stl_path), label=stl_path.name)
                    if asset_record:
                        customization_meta = {
                            "backend": backend.name,
                            "parameters": normalized,
                        }
                        if self._session.base_asset:
                            customization_meta["base_asset_path"] = str(self._session.base_asset.path)
                            customization_meta["base_asset_label"] = self._session.base_asset.label
                        if self._session.customization_id is not None:
                            customization_meta["id"] = self._session.customization_id
                        metadata = dict(asset_record.metadata)
                        metadata["customization"] = customization_meta
                        self._asset_service.update_asset(asset_record.id, metadata=metadata)

                self._status_label.setText("Model file updated.")
        except FileNotFoundError as exc:
            message = str(exc) or ("OpenSCAD executable not found. Ensure it is installed and on PATH.")
            self._status_label.setText(message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to update model")
            message = str(exc) or "Model update failed."
            self._status_label.setText(message)
        finally:
            self._update_action_states()

    def _handle_update_stl(self) -> None:
        if self._session is None:
            self._status_label.setText("Customizer session is not initialised.")
            return

        import shutil

        backend = self._session.backend
        schema = self._session.schema
        try:
            normalized = backend.validate(schema, self.parameter_values())
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or "Invalid parameter values supplied."
            self._status_label.setText(message)
            return

        source_path = self._session.source_path
        stl_path = source_path.with_suffix(".stl")

        self._status_label.setText("Updating STL…")
        self._preview_button.setEnabled(False)
        self._save_button.setEnabled(False)
        try:
            with tempfile.TemporaryDirectory(prefix="three_dfs_update_") as tmp:
                work_dir = Path(tmp)
                session = backend.plan_build(
                    self._session.source_path,
                    schema,
                    normalized,
                    output_dir=work_dir,
                    execute=True,
                    metadata={"intent": "update"},
                )
                mesh_path = self._select_mesh_artifact(session)
                if mesh_path is None or not mesh_path.exists():
                    raise FileNotFoundError("Updated model was not produced by the customizer.")
                shutil.copy2(mesh_path, stl_path)

                if self._asset_service:
                    asset_record = self._asset_service.ensure_asset(str(stl_path), label=stl_path.name)
                    if asset_record:
                        customization_meta = {
                            "backend": backend.name,
                            "parameters": normalized,
                        }
                        metadata = dict(asset_record.metadata)
                        metadata["customization"] = customization_meta
                        self._asset_service.update_asset(asset_record.id, metadata=metadata)

                self._status_label.setText("STL file updated.")
        except FileNotFoundError as exc:
            message = str(exc) or ("OpenSCAD executable not found. Ensure it is installed and on PATH.")
            self._status_label.setText(message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to update STL")
            message = str(exc) or "STL update failed."
            self._status_label.setText(message)
        finally:
            self._update_action_states()

    def _select_mesh_artifact(self, session: CustomizerSession) -> Path | None:
        for artifact in session.artifacts:
            path = Path(artifact.path)
            content_type = (artifact.content_type or "").lower()
            suffix = path.suffix.lower()
            if content_type.startswith("model/"):
                return path
            if suffix in {".stl", ".obj", ".ply", ".glb", ".gltf", ".3mf"}:
                return path
        return None

    def _handle_generate(self) -> None:
        button_text = self._save_button.text()
        if button_text == "Update Model":
            self._handle_update_model()
            return
        if button_text == "Update STL":
            self._handle_update_stl()
            return

        if self._session is None:
            self._status_label.setText("Customizer session is not initialised.")
            return
        if not self._can_save():
            self._status_label.setText("Customization requires managing this asset in the library.")
            return

        backend = self._session.backend
        schema = self._session.schema
        base_asset = self._session.base_asset
        assert base_asset is not None
        assert self._asset_service is not None

        try:
            normalized = backend.validate(schema, self.parameter_values())
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or "Invalid parameter values supplied."
            self._status_label.setText(message)
            self.customizationFailed.emit(message)
            return

        default_container_name = self._compose_container_name(base_asset, normalized)
        container_name = self._prompt_container_name(default_container_name)
        if container_name is None:
            self._status_label.setText("Save cancelled.")
            self._update_action_states()
            return

        self.customizationStarted.emit()
        self._reset_button.setEnabled(False)
        self._preview_button.setEnabled(False)
        self._save_button.setEnabled(False)
        try:
            result = execute_customization(
                base_asset,
                backend,
                normalized,
                asset_service=self._asset_service,
                container_name=container_name,
            )
        except FileNotFoundError as exc:
            message = "OpenSCAD executable not found. Ensure it is installed " "and available on the PATH."
            details = str(exc).strip()
            if details:
                message = f"{message} ({details})"
            self._status_label.setText(message)
            self.customizationFailed.emit(message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Customization failed")
            message = str(exc) or "Customization failed."
            self._status_label.setText(message)
            self.customizationFailed.emit(message)
        else:
            summary = self._summarize_parameters(normalized)
            if summary:
                message = f"Saved '{container_name}' with {summary}."
            else:
                message = f"Saved '{container_name}'."
            self._status_label.setText(message)
            self.customizationSucceeded.emit(result)
        finally:
            self._update_action_states()
