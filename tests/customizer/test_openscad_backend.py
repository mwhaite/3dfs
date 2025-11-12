from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from three_dfs.customizer.openscad import OpenSCADBackend


@pytest.fixture()
def fixture_path() -> Path:
    return Path(__file__).with_name("data") / "example.scad"


def test_load_schema_parses_descriptors(fixture_path: Path) -> None:
    backend = OpenSCADBackend()

    schema = backend.load_schema(fixture_path)

    assert [descriptor.name for descriptor in schema.parameters] == [
        "wall_thickness",
        "segments",
        "material",
        "use_logo",
    ]

    thickness, segments, material, use_logo = schema.parameters

    assert thickness.kind == "number"
    assert thickness.default == 2

    assert segments.kind == "range"
    assert segments.minimum == 3
    assert segments.maximum == 24
    assert segments.step == 1

    assert material.kind == "choice"
    assert material.choices == ("plastic", "steel", "wood")

    assert use_logo.kind == "boolean"
    assert use_logo.default is True


def test_validate_enforces_constraints(fixture_path: Path) -> None:
    backend = OpenSCADBackend()
    schema = backend.load_schema(fixture_path)

    normalized = backend.validate(
        schema,
        {"segments": 20, "material": "steel", "use_logo": "false"},
    )

    assert normalized["wall_thickness"] == 2
    assert normalized["segments"] == 20
    assert normalized["material"] == "steel"
    assert normalized["use_logo"] is False

    with pytest.raises(ValueError):
        backend.validate(schema, {"segments": 120})

    with pytest.raises(ValueError):
        backend.validate(schema, {"material": "glass"})


def test_plan_build_constructs_command_and_returns_session(fixture_path: Path, tmp_path: Path) -> None:
    backend = OpenSCADBackend()
    schema = backend.load_schema(fixture_path)

    overrides = {"segments": 10, "material": "wood", "use_logo": True}

    with patch("three_dfs.customizer.openscad.subprocess.run") as mocked_run:
        session = backend.plan_build(
            fixture_path,
            schema,
            overrides,
            output_dir=tmp_path / "build",
        )

    mocked_run.assert_not_called()

    assert session.session_id is None
    assert session.metadata["backend"] == "openscad"

    assert len(session.artifacts) == 2
    mesh_artifact = session.artifacts[0]
    assert mesh_artifact.path.endswith("example.stl")
    assert mesh_artifact.relationship == "output"

    source_artifact = session.artifacts[1]
    assert source_artifact.path.endswith("example_customized.scad")
    assert source_artifact.relationship == "source"
    assert source_artifact.content_type == "text/x-openscad"

    command = list(session.command)
    assert command[0] == "openscad"
    assert command[-1] == str(fixture_path)

    overrides_from_command = {
        command[index + 1].split("=", 1)[0]: command[index + 1].split("=", 1)[1]
        for index, token in enumerate(command)
        if token == "-D"
    }
    assert overrides_from_command["segments"] == "10"
    assert overrides_from_command["material"] == '"wood"'
    assert overrides_from_command["use_logo"] == "true"


def test_plan_build_writes_customized_source(fixture_path: Path, tmp_path: Path) -> None:
    backend = OpenSCADBackend()
    schema = backend.load_schema(fixture_path)
    overrides = {"wall_thickness": 4, "segments": 6, "material": "steel"}

    session = backend.plan_build(
        fixture_path,
        schema,
        overrides,
        output_dir=tmp_path / "build",
    )

    customized_source = Path(session.artifacts[1].path)
    assert customized_source.exists()
    text = customized_source.read_text(encoding="utf-8")
    assert "// Customized using three-dfs OpenSCAD backend." in text
    assert "wall_thickness = 4;" in text
    assert "segments = 6; // [3:1:24]" in text
    # Ensure material default updated while keeping comment annotation
    assert 'material = "steel"; // ["plastic", "steel", "wood"]' in text
