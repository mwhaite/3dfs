from __future__ import annotations

import uuid
from pathlib import Path

from three_dfs.application.container_scanner import scan_container_folder
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage


def _asset_service(tmp_path: Path) -> AssetService:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    return AssetService(repository)


def test_scan_container_preserves_existing_links(tmp_path):
    service = _asset_service(tmp_path)
    folder = tmp_path / str(uuid.uuid4())
    folder.mkdir()

    existing_links = [
        {
            "path": "/abs/derived",
            "label": "Derived",
            "kind": "link",
            "link_id": "abc123",
            "metadata": {
                "link_type": "customization",
                "link_target": "/abs/derived",
            },
        }
    ]

    existing = service.create_asset(
        str(folder),
        label="Container",
        metadata={"kind": "container", "links": existing_links},
    )

    outcome = scan_container_folder(folder, service, existing=existing)
    assert outcome is not None
    assert outcome.asset.metadata.get("links") == existing_links


def test_scan_container_preserves_linked_components(tmp_path):
    service = _asset_service(tmp_path)
    folder = tmp_path / str(uuid.uuid4())
    folder.mkdir()

    local_model = folder / "local-model.stl"
    local_model.write_text("solid test")

    linked_entry = {
        "path": "/remote/shared/model.stl",
        "label": "Remote Model",
        "kind": "linked_component",
        "metadata": {"link_import": {"source_container_id": 42}},
    }

    existing = service.create_asset(
        str(folder),
        label="Container",
        metadata={
            "kind": "container",
            "components": [linked_entry],
        },
    )

    refreshed = service.get_asset(existing.id)
    assert refreshed is not None

    outcome = scan_container_folder(folder, service, existing=refreshed)
    assert outcome is not None

    components = outcome.asset.metadata.get("components")
    assert isinstance(components, list)
    preserved = [entry for entry in components if entry.get("kind") == "linked_component"]
    assert len(preserved) == 1

    cloned_entry = preserved[0]
    original_entry = refreshed.metadata["components"][0]
    assert cloned_entry is not original_entry
    assert cloned_entry.get("metadata") is not original_entry.get("metadata")
    assert cloned_entry["metadata"]["link_import"]["source_container_id"] == 42
