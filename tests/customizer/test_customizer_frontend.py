from __future__ import annotations

import uuid
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)


from three_dfs.customizer import (
    CustomizerBackend,
    CustomizerSession,
    GeneratedArtifact,
    ParameterDescriptor,
    ParameterSchema,
)
from three_dfs.customizer.openscad import OpenSCADBackend
from three_dfs.customizer.pipeline import ArtifactResult, PipelineResult
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage
from three_dfs.ui.customizer_dialog import CustomizerDialog, CustomizerSessionConfig
from three_dfs.ui.customizer_panel import (
    BooleanParameterWidget,
    ChoiceParameterWidget,
    CustomizerPanel,
    CustomizerPreviewWidget,
    NumberParameterWidget,
    RangeParameterWidget,
)
from three_dfs.ui.preview_pane import PreviewPane, _build_preview_outcome, _PreviewMachineTagManager


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


def test_render_preview_runs_backend(qapp, tmp_path, monkeypatch):
    schema = ParameterSchema(
        parameters=(ParameterDescriptor(name="length", kind="number", default=5),),
        metadata={"backend": "dummy"},
    )

    called: dict[str, Any] = {}

    class PreviewBackend(CustomizerBackend):
        name = "preview"

        def load_schema(self, source: Path) -> ParameterSchema:  # pragma: no cover - unused
            return schema

        def validate(self, schema: ParameterSchema, values: Mapping[str, Any]) -> dict[str, Any]:
            return {"length": float(values.get("length", 5))}

        def plan_build(  # type: ignore[override]
            self,
            source: Path,
            schema: ParameterSchema,
            values: Mapping[str, Any],
            *,
            output_dir: Path,
            asset_service=None,
            execute: bool = False,
            metadata=None,
        ) -> CustomizerSession:
            nonlocal called
            called = {
                "execute": execute,
                "values": dict(values),
                "source": source,
            }
            mesh_path = output_dir / "preview.stl"
            mesh_path.write_text("solid preview\nendsolid preview\n", encoding="utf-8")
            artifact = GeneratedArtifact(
                path=str(mesh_path),
                label=mesh_path.name,
                relationship="output",
                content_type="model/stl",
            )
            return CustomizerSession(
                base_asset_path=str(source),
                schema=schema,
                parameters=dict(values),
                command=("preview",),
                artifacts=(artifact,),
            )

    backend = PreviewBackend()
    source = tmp_path / "preview.scad"
    source.write_text("length = 5;", encoding="utf-8")

    panel = CustomizerPanel()
    preview_widget = CustomizerPreviewWidget()
    panel.set_preview_widget(preview_widget)
    preview_signals: list[bool] = []
    panel.previewUpdated.connect(lambda: preview_signals.append(True))
    panel.set_session(backend=backend, schema=schema, source_path=source, base_asset=None)

    mesh_stub = SimpleNamespace(
        vertices=np.zeros((3, 3), dtype=np.float32),
        normals=np.zeros((3, 3), dtype=np.float32),
        indices=np.array([0, 1, 2], dtype=np.uint32),
        center=np.zeros(3, dtype=np.float32),
        radius=1.0,
    )
    monkeypatch.setattr("three_dfs.ui.customizer_panel.load_mesh_data", lambda path: (mesh_stub, None))

    panel._handle_preview()
    qapp.processEvents()

    assert called["execute"] is True
    assert preview_widget.has_preview() is True
    assert preview_widget.preview_parameters() == {"length": 5.0}
    assert preview_signals

    panel.deleteLater()
    preview_widget.deleteLater()


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
    assert dialog.panel().parameter_names() == tuple(descriptor.name for descriptor in schema.parameters)
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
    assert "customized containers available" in summary_text
    assert preview._customize_button.isVisible()
    assert not preview._customization_action_buttons
    preview.deleteLater()


def test_preview_pane_opens_customizer_for_scad(qapp, tmp_path):
    source = _fixture_path("example.scad")
    target = tmp_path / "example.scad"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    service = _create_asset_service(tmp_path)

    preview = PreviewPane(base_path=tmp_path, asset_service=service)
    preview._enqueue_preview = lambda path: preview._apply_outcome(_build_preview_outcome(path))

    preview.set_item(str(target), label="Example", metadata={}, asset_record=None)

    preview.show()
    qapp.processEvents()

    assert preview._tabs.currentIndex() == preview._customizer_tab_index
    assert preview._customizer_panel.parameter_names()
    assert preview._customizer_panel.can_execute is True

    preview.deleteLater()


def test_save_model_creates_container(qapp, tmp_path, monkeypatch):
    service = _create_asset_service(tmp_path)

    source_scad = tmp_path / "fixture.scad"
    source_scad.write_text("value = 1;", encoding="utf-8")
    base_asset = service.create_asset(str(source_scad), label="Fixture")

    schema = ParameterSchema(
        parameters=(ParameterDescriptor(name="value", kind="number", default=1),),
        metadata={"backend": "dummy"},
    )

    class DummyBackend(CustomizerBackend):
        name = "dummy"

        def load_schema(self, source: Path) -> ParameterSchema:  # pragma: no cover - unused
            return schema

        def validate(
            self,
            schema: ParameterSchema,
            values: dict[str, object],
        ) -> dict[str, object]:
            return {"value": float(values.get("value", 1))}

        def plan_build(self, *args, **kwargs):  # pragma: no cover - should not run
            raise AssertionError("plan_build should not be invoked during test")

    backend = DummyBackend()

    panel = CustomizerPanel(asset_service=service)
    monkeypatch.setattr(panel, "_prompt_container_name", lambda default: "Fixture Custom")
    panel.set_session(
        backend=backend,
        schema=schema,
        source_path=source_scad,
        base_asset=base_asset,
    )

    generated_model_path = tmp_path / "generated" / "fixture.stl"
    generated_model_path.parent.mkdir(parents=True, exist_ok=True)
    generated_model_path.write_text("solid fixture\nendsolid fixture\n", encoding="utf-8")
    generated_scad_path = tmp_path / "generated" / "fixture_custom.scad"
    generated_scad_path.write_text("value = 2;", encoding="utf-8")

    def fake_execute(
        base_asset_arg,
        backend_arg,
        parameters,
        *,
        asset_service,
        storage_root=None,
        cleanup=True,
    ) -> PipelineResult:
        customization = asset_service.create_customization(
            base_asset_arg.path,
            backend_identifier=backend_arg.name,
            parameter_schema=schema.to_dict(),
            parameter_values=dict(parameters),
        )

        model_asset, model_relationship = asset_service.record_derivative(
            customization.id,
            str(generated_model_path),
            relationship_type="output",
            label="Fixture Output",
            metadata={},
        )
        source_asset, source_relationship = asset_service.record_derivative(
            customization.id,
            str(generated_scad_path),
            relationship_type="source",
            label="Fixture Source",
            metadata={},
        )

        container_asset, container_path = asset_service.create_container(
            "Fixture Custom",
            root=tmp_path / "library",
            metadata={"created_from_customizer": True},
        )

        artifacts = (
            ArtifactResult(
                artifact=GeneratedArtifact(
                    path=str(generated_model_path),
                    label="Fixture Output",
                    relationship="output",
                    content_type="model/stl",
                ),
                asset=model_asset,
                relationship=model_relationship,
            ),
            ArtifactResult(
                artifact=GeneratedArtifact(
                    path=str(generated_scad_path),
                    label="Fixture Source",
                    relationship="source",
                    content_type="text/x-openscad",
                ),
                asset=source_asset,
                relationship=source_relationship,
            ),
        )

        return PipelineResult(
            base_asset=base_asset_arg,
            customization=customization,
            artifacts=artifacts,
            working_directory=generated_model_path.parent,
            output_path=str(generated_model_path),
            customization_id=customization.id,
            parameters=dict(parameters),
            generated_at=customization.created_at,
            container=container_asset,
            container_path=container_path,
        )

    monkeypatch.setattr(
        "three_dfs.ui.customizer_panel.execute_customization",
        fake_execute,
    )

    panel._handle_generate()
    qapp.processEvents()

    panel.deleteLater()


def test_customizer_panel_updates_existing_asset(qapp, tmp_path):
    service = _create_asset_service(tmp_path)

    source_scad = tmp_path / "fixture.scad"
    source_scad.write_text("module fixture(size=1) {}", encoding="utf-8")

    base_asset = service.create_asset(str(source_scad), label="Fixture")
    schema = ParameterSchema(parameters=(ParameterDescriptor(name="size", kind="number", default=1.0),))

    customization = service.create_customization(
        base_asset.path,
        backend_identifier="dummy",
        parameter_schema=schema.to_dict(),
        parameter_values={"size": 1.0},
    )

    target_path = tmp_path / "fixture.stl"
    target_path.write_text("old", encoding="utf-8")
    target_meta = {
        "customization": {
            "id": customization.id,
            "backend": "dummy",
            "base_asset_path": base_asset.path,
            "base_asset_label": base_asset.label,
            "relationship": "output",
            "parameters": {"size": 1.0},
        }
    }
    target_asset, _ = service.record_derivative(
        customization.id,
        str(target_path),
        relationship_type="output",
        label="Fixture Output",
        metadata=target_meta,
    )

    class UpdatingBackend(CustomizerBackend):
        name = "dummy"

        def load_schema(self, source: Path) -> ParameterSchema:
            return schema

        def validate(
            self,
            schema: ParameterSchema,
            values: Mapping[str, Any],
        ) -> dict[str, Any]:
            return {"size": float(values.get("size", 1.0))}

        def plan_build(
            self,
            source: Path,
            schema: ParameterSchema,
            values: Mapping[str, Any],
            *,
            output_dir: Path,
            execute: bool = False,
            metadata: Mapping[str, Any] | None = None,
            asset_service: AssetService | None = None,
        ) -> CustomizerSession:
            normalized = self.validate(schema, values)
            output_dir.mkdir(parents=True, exist_ok=True)
            mesh_path = output_dir / "updated.stl"
            if execute:
                mesh_path.write_text("new-mesh", encoding="utf-8")
            artifact = GeneratedArtifact(
                path=str(mesh_path),
                label="Updated",
                relationship="output",
                content_type="model/stl",
            )
            return CustomizerSession(
                base_asset_path=str(source),
                schema=schema,
                parameters=normalized,
                command=("dummy", str(source)),
                artifacts=(artifact,),
                metadata=metadata or {},
            )

    backend = UpdatingBackend()
    panel = CustomizerPanel(asset_service=service)
    panel.set_session(
        backend=backend,
        schema=schema,
        source_path=source_scad,
        base_asset=base_asset,
        values={"size": 2.0},
        derivative_path=target_path,
        customization_id=customization.id,
    )

    panel._handle_generate()
    qapp.processEvents()

    assert target_path.read_text(encoding="utf-8") == "new-mesh"
    refreshed_asset = service.get_asset(target_asset.id)
    assert refreshed_asset is not None
    assert refreshed_asset.metadata["customization"]["parameters"]["size"] == 2.0

    refreshed_customization = service.get_customization(customization.id)
    assert refreshed_customization is not None
    assert refreshed_customization.parameter_values["size"] == 2.0

    panel.deleteLater()


def test_machine_tags_propagate_to_container(tmp_path):
    service = _create_asset_service(tmp_path)

    container_dir = tmp_path / str(uuid.uuid4())
    container_dir.mkdir()
    container_asset = service.create_asset(
        str(container_dir),
        label="Container",
        metadata={"kind": "container"},
    )

    model_path = container_dir / "customized_model.stl"
    model_path.write_text("mesh", encoding="utf-8")

    manager = _PreviewMachineTagManager(service)
    manager.update_machine_tags(
        asset_path=str(model_path),
        assign=["machine:mk3"],
        remove=[],
        rename={},
    )

    model_asset = service.get_asset_by_path(str(model_path))
    assert model_asset is not None
    assert "machine:mk3" in service.tags_for_asset(model_asset.id)
    assert "machine:mk3" in service.tags_for_asset(container_asset.id)


def test_tag_sidebar_lists_derivatives(qapp, tmp_path):
    service = _create_asset_service(tmp_path)

    source = tmp_path / "item.scad"
    source.write_text("module test() {}", encoding="utf-8")
    service.create_asset(str(source), label="Item")
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
