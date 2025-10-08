from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtTest", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtTest import QSignalSpy

from three_dfs.customizer.openscad import OpenSCADBackend
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage
from three_dfs.ui.customizer_dialog import CustomizerDialog, CustomizerSessionConfig
from three_dfs.ui.customizer_panel import (
    BooleanParameterWidget,
    ChoiceParameterWidget,
    CustomizerPanel,
    NumberParameterWidget,
    RangeParameterWidget,
)
from three_dfs.ui.preview_pane import PreviewPane


def _fixture_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "data" / name


def _create_asset_service(root: Path) -> AssetService:
    storage = SQLiteStorage(root / "assets.sqlite3")
    repository = AssetRepository(storage)
    return AssetService(repository)


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


def test_customizer_dialog_configures_panel(qapp, tmp_path):
    backend = OpenSCADBackend()
    source = _fixture_path("example.scad")
    target = tmp_path / "example.scad"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    service = _create_asset_service(tmp_path)
    base_asset = service.create_asset(str(target), label="Example Part")
    schema = backend.load_schema(target)

    config = CustomizerSessionConfig(
        backend=backend,
        schema=schema,
        source_path=target,
        base_asset=base_asset,
        values={"segments": 8},
    )

    dialog = CustomizerDialog(asset_service=service)
    dialog.set_session(config)

    assert "Example Part" in dialog.windowTitle()
    assert dialog.panel().parameter_names() == tuple(
        descriptor.name for descriptor in schema.parameters
    )
    assert dialog.panel().parameter_values()["segments"] == 8
    dialog.close()


def test_preview_pane_displays_customization_summary(qapp, tmp_path):
    backend = OpenSCADBackend()
    source = _fixture_path("example.scad")
    target = tmp_path / "example.scad"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    service = _create_asset_service(tmp_path)
    base_asset = service.create_asset(str(target), label="Example")
    schema = backend.load_schema(target)
    customization = service.create_customization(
        str(target),
        backend_identifier="openscad",
        parameter_schema=schema.to_dict(),
        parameter_values={"segments": 12},
    )

    derivative_path = tmp_path / "example_output.stl"
    derivative_path.write_text("mesh", encoding="utf-8")
    derivative_metadata = {
        "customization": {
            "id": customization.id,
            "backend": "openscad",
            "base_asset_path": str(target),
            "relationship": "output",
            "parameters": {"segments": 12},
        }
    }
    service.record_derivative(
        customization.id,
        str(derivative_path),
        relationship_type="output",
        label="Output",
        metadata=derivative_metadata,
    )

    preview = PreviewPane(base_path=tmp_path, asset_service=service)
    preview._enqueue_preview = lambda path: None

    preview.set_item(
        str(target),
        label=base_asset.label,
        metadata=base_asset.metadata,
        asset_record=base_asset,
    )
    preview.show()
    qapp.processEvents()

    assert preview.can_customize is True
    summary_text = preview._customization_summary_label.text().lower()
    assert "customized artifact" in summary_text
    assert preview._customize_button.isVisible()
    assert preview._customization_action_buttons

    first_button = preview._customization_action_buttons[0]
    spy = QSignalSpy(preview.navigationRequested)
    first_button.click()
    qapp.processEvents()
    assert spy.count() == 1
    assert spy.at(0)[0] == str(derivative_path)
    preview.deleteLater()


def test_tag_sidebar_lists_derivatives(qapp, tmp_path):
    service = _create_asset_service(tmp_path)

    source = tmp_path / "item.scad"
    source.write_text("module test() {}", encoding="utf-8")
    base_asset = service.create_asset(str(source), label="Item")
    customization = service.create_customization(
        str(source),
        backend_identifier="openscad",
        parameter_schema={},
        parameter_values={},
    )
    derivative_path = tmp_path / "item_output.stl"
    derivative_path.write_text("mesh", encoding="utf-8")
    derivative_metadata = {
        "customization": {
            "id": customization.id,
            "backend": "openscad",
            "base_asset_path": str(source),
            "relationship": "output",
        }
    }
    service.record_derivative(
        customization.id,
        str(derivative_path),
        relationship_type="output",
        label="Output",
        metadata=derivative_metadata,
    )
