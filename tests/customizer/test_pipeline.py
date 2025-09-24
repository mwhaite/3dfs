from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from three_dfs.customizer import (
    CustomizerBackend,
    CustomizerSession,
    GeneratedArtifact,
    ParameterDescriptor,
    ParameterSchema,
    execute_customization,
)
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage

_MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0cIDATx\x9cc``\x00\x00\x00\x04"
    b"\x00\x01\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
)


@dataclass
class StubBackend(CustomizerBackend):
    """Test backend that emits a mesh artifact and a preview image."""

    name: str = "stub-backend"

    def load_schema(self, source: Path) -> ParameterSchema:
        descriptor = ParameterDescriptor(
            name="size",
            kind="number",
            default=1.0,
            description="Size multiplier",
        )
        return ParameterSchema(
            parameters=(descriptor,),
            metadata={"source": str(source)},
        )

    def validate(
        self,
        schema: ParameterSchema,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            size = float(values.get("size", schema.parameters[0].default))
        except (TypeError, ValueError):
            size = float(schema.parameters[0].default)
        return {"size": size}

    def plan_build(
        self,
        source: Path,
        schema: ParameterSchema,
        values: Mapping[str, Any],
        *,
        output_dir: Path,
        asset_service: AssetService | None = None,
        execute: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> CustomizerSession:
        normalized = self.validate(schema, values)
        output_dir.mkdir(parents=True, exist_ok=True)

        model_path = output_dir / "customized_model.stl"
        preview_path = output_dir / "customized_preview.png"

        if execute:
            model_path.write_text("solid stub\nendsolid stub\n", encoding="utf-8")
            preview_path.write_bytes(_MINIMAL_PNG)

        artifacts = (
            GeneratedArtifact(
                path=str(model_path),
                label="Customized Model",
                relationship="output",
                content_type="model/stl",
            ),
            GeneratedArtifact(
                path=str(preview_path),
                label="Customization Preview",
                relationship="preview",
                content_type="image/png",
            ),
        )

        session_metadata = {"backend": self.name}
        if metadata:
            session_metadata.update(metadata)

        return CustomizerSession(
            base_asset_path=str(source),
            schema=schema,
            parameters=normalized,
            command=("stub", "--size", str(normalized["size"]), str(source)),
            artifacts=artifacts,
            metadata=session_metadata,
        )


@dataclass
class ScriptReferenceBackend(StubBackend):
    """Stub backend that also links an existing Python script artifact."""

    script_asset_id: int | None = None
    script_path: Path | None = None

    def plan_build(
        self,
        source: Path,
        schema: ParameterSchema,
        values: Mapping[str, Any],
        *,
        output_dir: Path,
        asset_service: AssetService | None = None,
        execute: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> CustomizerSession:
        session = super().plan_build(
            source,
            schema,
            values,
            output_dir=output_dir,
            asset_service=asset_service,
            execute=execute,
            metadata=metadata,
        )

        artifacts = list(session.artifacts)
        if self.script_asset_id is not None and self.script_path is not None:
            artifacts.append(
                GeneratedArtifact(
                    path=str(self.script_path),
                    label="Build123 Script",
                    relationship="script",
                    asset_id=self.script_asset_id,
                    content_type="text/x-python",
                )
            )

        session_metadata = dict(session.metadata)
        if self.script_path is not None:
            session_metadata.setdefault("python", {})
            python_meta = dict(session_metadata["python"])
            python_meta.setdefault("scripts", []).append(str(self.script_path))
            session_metadata["python"] = python_meta

        return CustomizerSession(
            base_asset_path=session.base_asset_path,
            schema=session.schema,
            parameters=dict(session.parameters),
            command=session.command,
            artifacts=tuple(artifacts),
            session_id=session.session_id,
            metadata=session_metadata,
        )


@pytest.fixture()
def asset_service(tmp_path: Path) -> AssetService:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    return AssetService(repository)


def test_execute_customization_registers_artifacts(
    tmp_path: Path,
    asset_service: AssetService,
) -> None:
    backend = StubBackend()

    base_source = tmp_path / "sources" / "base.scad"
    base_source.parent.mkdir(parents=True, exist_ok=True)
    base_source.write_text("module stub() {}\n", encoding="utf-8")

    base_asset = asset_service.create_asset(
        str(base_source),
        label="Base Fixture",
        metadata={"managed_path": str(base_source), "source_type": "local"},
    )

    library_root = tmp_path / "library"

    result = execute_customization(
        base_asset,
        backend,
        {"size": 2},
        asset_service=asset_service,
        storage_root=library_root,
    )

    assert result.customization.backend_identifier == backend.name
    assert result.customization.parameter_values == {"size": 2.0}
    assert not result.working_directory.exists()

    artifacts_by_relationship = {
        item.artifact.relationship: item for item in result.artifacts
    }
    assert set(artifacts_by_relationship) == {"output", "preview"}

    output_result = artifacts_by_relationship["output"]
    preview_result = artifacts_by_relationship["preview"]

    expected_root = (
        library_root
        / "customizations"
        / str(base_asset.id)
        / str(result.customization.id)
    )
    assert Path(output_result.asset.path).parent == expected_root
    assert Path(preview_result.asset.path).parent == expected_root

    output_metadata = output_result.asset.metadata
    customization_meta = output_metadata["customization"]
    assert customization_meta["id"] == result.customization.id
    assert customization_meta["parameters"] == {"size": 2.0}
    assert customization_meta["relationship"] == "output"
    assert customization_meta["base_asset_label"] == base_asset.label
    assert (
        customization_meta["base_asset_updated_at"] == base_asset.updated_at.isoformat()
    )

    recorded_source = customization_meta.get("source_modified_at")
    assert isinstance(recorded_source, str)
    recorded_dt = datetime.fromisoformat(recorded_source)
    expected_dt = datetime.fromtimestamp(base_source.stat().st_mtime, tz=UTC)
    assert recorded_dt == expected_dt

    previews = customization_meta.get("previews", [])
    assert previews and any(
        entry["asset_id"] == preview_result.asset.id for entry in previews
    )

    preview_metadata = preview_result.asset.metadata["customization"]
    assert preview_metadata["is_preview"] is True
    assert preview_metadata["relationship"] == "preview"

    relationships = asset_service.repository.list_relationships_for_base_asset(
        base_asset.id
    )
    mapping = {
        (relation.generated_asset_id, relation.relationship_type)
        for relation in relationships
    }
    assert mapping == {
        (output_result.asset.id, "output"),
        (preview_result.asset.id, "preview"),
    }


def test_execute_customization_links_python_script(
    tmp_path: Path, asset_service: AssetService
) -> None:
    backend = ScriptReferenceBackend()

    script_path = tmp_path / "scripts" / "customizer.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("print('build123 script')\n", encoding="utf-8")

    script_asset = asset_service.create_asset(
        str(script_path),
        label="Customizer Script",
        metadata={"kind": "python-script"},
    )

    backend.script_asset_id = script_asset.id
    backend.script_path = script_path

    base_source = tmp_path / "sources" / "base_model.scad"
    base_source.parent.mkdir(parents=True, exist_ok=True)
    base_source.write_text("module stub() {}\n", encoding="utf-8")

    base_asset = asset_service.create_asset(
        str(base_source),
        label="Base Fixture",
        metadata={"managed_path": str(base_source), "source_type": "local"},
    )

    library_root = tmp_path / "library"

    result = execute_customization(
        base_asset,
        backend,
        {"size": 1.5},
        asset_service=asset_service,
        storage_root=library_root,
    )

    artifacts_by_relationship = {
        item.artifact.relationship: item for item in result.artifacts
    }
    assert set(artifacts_by_relationship) == {"output", "preview", "script"}

    script_result = artifacts_by_relationship["script"]
    assert script_result.asset.id == script_asset.id
    assert Path(script_result.asset.path) == script_path
    assert script_result.asset.metadata == script_asset.metadata

    expected_root = (
        library_root
        / "customizations"
        / str(base_asset.id)
        / str(result.customization.id)
    )

    output_result = artifacts_by_relationship["output"]
    assert Path(output_result.asset.path).parent == expected_root
    preview_result = artifacts_by_relationship["preview"]
    assert Path(preview_result.asset.path).parent == expected_root

    relationships = asset_service.repository.list_relationships_for_base_asset(
        base_asset.id
    )
    mapping = {
        (relation.generated_asset_id, relation.relationship_type)
        for relation in relationships
    }
    assert mapping == {
        (output_result.asset.id, "output"),
        (preview_result.asset.id, "preview"),
        (script_asset.id, "script"),
    }

    stored_script = asset_service.repository.get_asset(script_asset.id)
    assert stored_script is not None
    assert stored_script.metadata == script_asset.metadata
