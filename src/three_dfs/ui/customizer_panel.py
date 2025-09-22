"""UI helpers for interacting with customizer backends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..customizer import (
    CustomizerBackend,
    ParameterDescriptor,
    ParameterSchema,
    execute_customization,
)
from ..storage import AssetRecord, AssetService

__all__ = [
    "BooleanParameterWidget",
    "ChoiceParameterWidget",
    "CustomizerPanel",
    "NumberParameterWidget",
    "RangeParameterWidget",
    "StringParameterWidget",
]


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

    def __init__(
        self, descriptor: ParameterDescriptor, parent: QWidget | None = None
    ) -> None:
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

    def __init__(
        self, descriptor: ParameterDescriptor, parent: QWidget | None = None
    ) -> None:
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

    def __init__(
        self, descriptor: ParameterDescriptor, parent: QWidget | None = None
    ) -> None:
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

    def __init__(
        self, descriptor: ParameterDescriptor, parent: QWidget | None = None
    ) -> None:
        super().__init__(descriptor, parent)

        self._is_integer = _is_integer_descriptor(descriptor)
        minimum = (
            descriptor.minimum if descriptor.minimum is not None else descriptor.default
        )
        maximum = (
            descriptor.maximum if descriptor.maximum is not None else descriptor.default
        )
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
        position = (
            int(round((coerced - self._minimum) / self._step)) if self._step else 0
        )
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

    def __init__(
        self, descriptor: ParameterDescriptor, parent: QWidget | None = None
    ) -> None:
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

    def __init__(
        self, descriptor: ParameterDescriptor, parent: QWidget | None = None
    ) -> None:
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


class CustomizerPanel(QWidget):
    """Container widget exposing sliders and toggles for a schema."""

    customizationStarted = Signal()
    customizationSucceeded = Signal(object)
    customizationFailed = Signal(str)

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

        self._intro_label = QLabel(
            "Adjust the parameters below to customise this design.", self
        )
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
        self._apply_button = QPushButton("Generate", self)
        self._apply_button.clicked.connect(self._handle_generate)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        button_row.addStretch(1)
        button_row.addWidget(self._reset_button)
        button_row.addWidget(self._apply_button)

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
        self._reset_button.setEnabled(False)
        self._apply_button.setEnabled(False)

    def set_session(
        self,
        *,
        backend: CustomizerBackend,
        schema: ParameterSchema,
        source_path: str | Path,
        base_asset: AssetRecord | None = None,
        values: Mapping[str, Any] | None = None,
    ) -> None:
        """Populate the panel using *schema* from *backend*."""

        self.clear()
        source = Path(source_path)
        self._session = _SessionContext(
            backend=backend,
            schema=schema,
            source_path=source,
            base_asset=base_asset,
        )

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
        self._reset_button.setEnabled(has_parameters)
        can_execute = (
            has_parameters
            and self._asset_service is not None
            and base_asset is not None
        )
        self._apply_button.setEnabled(can_execute)

        if not has_parameters:
            self._status_label.setText("No customizable parameters found in this file.")
        elif not can_execute:
            self._status_label.setText(
                "Register this part in the library to generate customized builds."
            )
        else:
            self._status_label.clear()

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

    @property
    def can_execute(self) -> bool:
        """Return ``True`` when the Generate action is available."""

        return self._apply_button.isEnabled()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _handle_value_changed(self, _value: object) -> None:
        del _value
        if self._status_label.text():
            self._status_label.clear()

    def _handle_generate(self) -> None:
        if not self._session:
            self._status_label.setText("Customizer session is not initialised.")
            return
        if not self._apply_button.isEnabled():
            self._status_label.setText(
                "Customization requires managing this asset in the library."
            )
            return

        parameters = self.parameter_values()
        backend = self._session.backend
        schema = self._session.schema
        base_asset = self._session.base_asset
        assert base_asset is not None  # guarded by button enabled state
        assert self._asset_service is not None

        try:
            normalized = backend.validate(schema, parameters)
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or "Invalid parameter values supplied."
            self._status_label.setText(message)
            self.customizationFailed.emit(message)
            return

        self.customizationStarted.emit()
        try:
            result = execute_customization(
                base_asset,
                backend,
                normalized,
                asset_service=self._asset_service,
            )
        except FileNotFoundError as exc:
            message = "OpenSCAD executable not found. Ensure it is installed and available on the PATH."
            details = str(exc).strip()
            if details:
                message = f"{message} ({details})"
            self._status_label.setText(message)
            self.customizationFailed.emit(message)
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or "Customization failed."
            self._status_label.setText(message)
            self.customizationFailed.emit(message)
        else:
            artifact_count = len(result.artifacts)
            if artifact_count == 1:
                summary = "Generated 1 customized artifact."
            else:
                summary = f"Generated {artifact_count} customized artifacts."
            self._status_label.setText(summary)
            self.customizationSucceeded.emit(result)
