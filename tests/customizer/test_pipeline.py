from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest

from three_dfs.customizer import (
    GeneratedArtifact,
    ParameterDescriptor,
    ParameterSchema,
    execute_customization,
)
from three_dfs.customizer import CustomizerBackend
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage


_MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0cIDATx\x9cc``\x00\x00\x00\x04"
    b"\x00\x01\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
)


@dataclass(slots=True)
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
        return ParameterSchema(parameters=(descriptor,), metadata={"source": str(source)})

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
    ) -> "CustomizerSession":
        from three_dfs.customizer import CustomizerSession

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


@pytest.fixture()
def asset_service(tmp_path: Path) -> AssetService:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    return AssetService(repository)


def test_execute_customization_registers_artifacts(tmp_path: Path, asset_service: AssetService) -> None:
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

    previews = customization_meta.get("previews", [])
    assert previews and any(entry["asset_id"] == preview_result.asset.id for entry in previews)

    preview_metadata = preview_result.asset.metadata["customization"]
    assert preview_metadata["is_preview"] is True
    assert preview_metadata["relationship"] == "preview"

    relationships = asset_service.repository.list_relationships_for_base_asset(
        base_asset.id
    )
    mapping = {
        (relation.generated_asset_id, relation.relationship_type) for relation in relationships
    }
    assert mapping == {
        (output_result.asset.id, "output"),
        (preview_result.asset.id, "preview"),
    }

