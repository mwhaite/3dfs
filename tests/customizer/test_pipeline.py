from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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
from three_dfs.storage.container_service import ContainerService

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
    pass


def test_execute_customization_links_python_script(tmp_path: Path, asset_service: AssetService) -> None:
    pass


def test_execute_customization_links_to_source_container(tmp_path: Path, asset_service: AssetService) -> None:
    container_service = ContainerService(asset_service)
    library_root = tmp_path / "library"
    base_container, base_folder = container_service.create_container(
        "Example Base",
        root=library_root,
    )

    source_path = base_folder / "example.scad"
    source_path.write_text("module example() {}", encoding="utf-8")

    base_asset = asset_service.create_asset(
        str(source_path),
        label="Example",
    )

    result = execute_customization(
        base_asset,
        StubBackend(),
        {"size": 1.5},
        asset_service=asset_service,
        storage_root=tmp_path / "managed",
    )

    assert result.container is not None

    refreshed_source = asset_service.get_asset(base_container.id)
    refreshed_target = asset_service.get_asset(result.container.id)

    assert refreshed_source is not None
    assert refreshed_target is not None

    links = refreshed_source.metadata.get("links")
    assert isinstance(links, list) and links
    outgoing = links[-1]
    assert outgoing.get("path") == refreshed_target.path
    metadata = outgoing.get("metadata") or {}
    assert metadata.get("link_type") == "customization"
    assert metadata.get("target_container_id") == refreshed_target.id

    linked_from = refreshed_target.metadata.get("linked_from")
    assert isinstance(linked_from, list) and linked_from
    incoming = linked_from[-1]
    assert incoming.get("source_container_id") == refreshed_source.id
