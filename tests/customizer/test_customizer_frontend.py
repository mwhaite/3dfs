from __future__ import annotations

from pathlib import Path

import pytest

from three_dfs.customizer.openscad import OpenSCADBackend
from three_dfs.ui.customizer_panel import (
    BooleanParameterWidget,
    ChoiceParameterWidget,
    CustomizerPanel,
    NumberParameterWidget,
    RangeParameterWidget,
)


def _fixture_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "data" / name


def test_customizer_panel_renders_schema(qapp):
    backend = OpenSCADBackend()
    source = _fixture_path("example.scad")
    schema = backend.load_schema(source)

    panel = CustomizerPanel()
    panel.set_session(backend=backend, schema=schema, source_path=source)

    assert panel.parameter_names() == (
        "wall_thickness",
        "segments",
        "material",
        "use_logo",
    )
    assert panel.can_execute is False

    widgets = {name: panel.editor(name) for name in panel.parameter_names()}

    assert isinstance(widgets["wall_thickness"], NumberParameterWidget)
    assert isinstance(widgets["segments"], RangeParameterWidget)
    assert isinstance(widgets["material"], ChoiceParameterWidget)
    assert isinstance(widgets["use_logo"], BooleanParameterWidget)

    values = panel.parameter_values()
    assert values["wall_thickness"] == 2
    assert values["segments"] == 12
    assert values["material"] == "plastic"
    assert values["use_logo"] is True

    segments_widget = widgets["segments"]
    assert pytest.approx(segments_widget.minimum) == 3
    assert pytest.approx(segments_widget.maximum) == 24
    assert pytest.approx(segments_widget.step) == 1

    segments_widget.set_value(6)
    assert panel.parameter_values()["segments"] == 6
    segments_widget.set_value(100)
    assert panel.parameter_values()["segments"] == 24

    material_widget = widgets["material"]
    material_widget.set_value("steel")
    assert panel.parameter_values()["material"] == "steel"

    logo_widget = widgets["use_logo"]
    logo_widget.set_value(False)
    assert panel.parameter_values()["use_logo"] is False

    panel.reset_parameters()
    reset_values = panel.parameter_values()
    assert reset_values["segments"] == 12
    assert reset_values["material"] == "plastic"
