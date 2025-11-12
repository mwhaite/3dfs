from __future__ import annotations

from pathlib import Path

from three_dfs.container import build_linked_component_entry
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
