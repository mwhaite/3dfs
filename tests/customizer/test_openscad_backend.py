from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from three_dfs.customizer.openscad import OpenSCADBackend
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage


@pytest.fixture()
def fixture_path() -> Path:
    return Path(__file__).with_name("data") / "example.scad"


@pytest.fixture()
def asset_service(tmp_path: Path) -> AssetService:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    return AssetService(repository)


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


def test_plan_build_constructs_command_and_persists_session(
    fixture_path: Path, asset_service: AssetService, tmp_path: Path
) -> None:
    backend = OpenSCADBackend()
    schema = backend.load_schema(fixture_path)

    overrides = {"segments": 10, "material": "wood", "use_logo": True}

    with patch("three_dfs.customizer.openscad.subprocess.run") as mocked_run:
        session = backend.plan_build(
            fixture_path,
            schema,
            overrides,
            output_dir=tmp_path / "build",
            asset_service=asset_service,
        )

    mocked_run.assert_not_called()

    assert session.session_id is not None
    assert session.metadata["backend"] == "openscad"

    artifact = session.artifacts[0]
    assert artifact.asset_id is not None
    assert artifact.path.endswith("example.stl")

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

    stored = asset_service.get_customization_session(session.session_id)
    assert stored is not None
    assert stored.parameters == session.parameters
    assert stored.artifacts[0].asset_id == artifact.asset_id

    listing = asset_service.list_customization_sessions(str(fixture_path))
    assert [item.session_id for item in listing] == [session.session_id]
