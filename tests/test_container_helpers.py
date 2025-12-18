from __future__ import annotations

from pathlib import Path

from three_dfs.container import (
    apply_container_metadata,
    build_linked_component_entry,
    get_container_metadata,
)
from three_dfs.container_metadata import PrintedStatus
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage


def _service(tmp_path: Path) -> AssetService:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    return AssetService(repository)


def test_build_linked_component_entry_embeds_link_metadata(tmp_path):
    service = _service(tmp_path)
    container_dir = tmp_path / "source"
    container_dir.mkdir()
    source_container = service.create_asset(
        str(container_dir),
        label="Source Container",
        metadata={"kind": "container", "display_name": "Source"},
    )

    component_payload = {
        "path": str(tmp_path / "remote" / "model.stl"),
        "label": "Model",
        "asset_id": 123,
        "relative_path": "model.stl",
        "metadata": {"handler": "system"},
    }

    entry = build_linked_component_entry(component_payload, source_container)

    assert entry["kind"] == "linked_component"
    assert entry["path"] == component_payload["path"]
    assert entry.get("asset_id") == 123
    assert entry.get("relative_path") == "model.stl"

    metadata = entry.get("metadata")
    assert isinstance(metadata, dict)
    link_meta = metadata.get("link_import")
    assert isinstance(link_meta, dict)
    assert link_meta["source_container_id"] == source_container.id
    assert link_meta["source_container_label"] == "Source"
    assert link_meta["source_component_path"] == component_payload["path"]
    assert link_meta["link_import_id"]


def test_get_container_metadata_defaults_when_missing(tmp_path):
    service = _service(tmp_path)
    container_dir = tmp_path / "container"
    container_dir.mkdir()
    container = service.create_asset(str(container_dir), metadata={"kind": "container"})

    meta = get_container_metadata(container)
    assert meta.printed_status is PrintedStatus.NOT_STARTED
    assert meta.contacts == []


def test_apply_container_metadata_embeds_payload():
    base = {"kind": "container", "display_name": "Widget"}
    merged = apply_container_metadata(
        base,
        {
            "printed_status": "printed",
            "priority": "high",
            "contacts": [{"name": "Ru", "email": "ru@example.com"}],
        },
    )

    assert merged["kind"] == "container"
    container_meta = get_container_metadata(merged)
    assert container_meta.printed_status is PrintedStatus.PRINTED
    assert container_meta.priority.value == "high"
    assert container_meta.contacts[0].name == "Ru"
