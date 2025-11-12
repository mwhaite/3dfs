from __future__ import annotations

from pathlib import Path

from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage
from three_dfs.storage.container_service import ContainerService


def _service(tmp_path: Path) -> AssetService:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    return AssetService(repository)


def test_refresh_link_references_updates_metadata(tmp_path):
    service = _service(tmp_path)
    container_service = ContainerService(service)

    source, _ = container_service.create_container("Source", root=tmp_path)
    target, _ = container_service.create_container("Target", root=tmp_path)

    container_service.link_containers(source, target, link_type="link")

    latest_target = service.get_asset(target.id)
    renamed = service.update_asset(
        latest_target.id,
        label="Renamed",
        metadata={**latest_target.metadata, "display_name": "Renamed"},
    )

    container_service.refresh_link_references(renamed)

    updated_source = service.get_asset(source.id)
    assert updated_source is not None
    links = updated_source.metadata.get("links")
    assert isinstance(links, list) and links
    assert links[0]["label"] == "Renamed"
    assert links[0]["path"] == renamed.path

    updated_target = service.get_asset(renamed.id)
    assert updated_target is not None
    linked_from = updated_target.metadata.get("linked_from")
    assert isinstance(linked_from, list) and linked_from
    assert linked_from[0]["source_label"] == "Source"


def test_link_containers_tracks_version_metadata(tmp_path):
    service = _service(tmp_path)
    container_service = ContainerService(service)

    source, _ = container_service.create_container("Source", root=tmp_path)
    target, _ = container_service.create_container("Target", root=tmp_path)

    snapshot = service.create_container_version(
        target.id,
        name="v1",
        metadata=target.metadata,
    )

    container_service.link_containers(
        source,
        target,
        link_type="link",
        target_version_id=snapshot.id,
    )

    updated_source = service.get_asset(source.id)
    assert updated_source is not None
    link_entry = updated_source.metadata.get("links")[0]
    assert link_entry["target_version_id"] == snapshot.id
    metadata = link_entry.get("metadata")
    assert metadata["target_version_id"] == snapshot.id
    assert metadata["target_version_name"] == "v1"

    updated_target = service.get_asset(target.id)
    assert updated_target is not None
    incoming = updated_target.metadata.get("linked_from")[0]
    assert incoming["target_version_id"] == snapshot.id


def test_link_containers_defaults_to_latest_version(tmp_path):
    service = _service(tmp_path)
    container_service = ContainerService(service)

    source, _ = container_service.create_container("Source", root=tmp_path)
    target, _ = container_service.create_container("Target", root=tmp_path)

    service.create_container_version(target.id, name="v1", metadata=target.metadata)
    latest = service.create_container_version(
        target.id,
        name="v2",
        metadata={**target.metadata, "components": [{"path": "foo", "label": "Foo"}]},
    )

    container_service.link_containers(source, target, link_type="link")

    link_entry = service.get_asset(source.id).metadata.get("links")[0]
    assert link_entry["target_version_id"] == latest.id
