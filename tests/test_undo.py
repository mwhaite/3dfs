"""Tests for undo/redo utilities."""

from __future__ import annotations

from three_dfs.storage import AssetRepository, AssetService, UNDO_VERSION_NOTE
from three_dfs.storage.database import SQLiteStorage
from three_dfs.utils.undo import ActionHistory


def test_undo_restores_deleted_file_and_metadata(tmp_path):
    container_root = tmp_path / "library"
    container_root.mkdir()
    db_path = tmp_path / "db.sqlite"
    storage = SQLiteStorage(db_path)
    repository = AssetRepository(storage)
    service = AssetService(repository)
    history = ActionHistory(history_path=tmp_path / "history.json", trash_root=tmp_path / "trash")

    container_path = container_root / "container"
    container_path.mkdir()
    target_file = container_path / "data.txt"
    target_file.write_text("payload")

    container_asset = service.create_asset(
        str(container_path),
        label="Container",
        metadata={"files": [{"path": str(target_file)}]},
    )
    trash_path = history.trash_file(target_file)
    history.record_deletion(
        kind="file",
        original_path=target_file,
        trash_path=trash_path,
        container_asset_id=container_asset.id,
        container_asset_path=container_asset.path,
        container_metadata=container_asset.metadata,
        asset_snapshot={
            "path": str(target_file),
            "label": "data.txt",
            "metadata": {},
            "tags": [],
        },
        asset_service=service,
    )

    # Simulate removal from metadata
    service.update_asset(container_asset.id, metadata={})

    message = history.undo_last(asset_service=service)

    assert message is not None
    assert target_file.exists()
    restored_container = service.get_asset(container_asset.id)
    assert restored_container is not None
    assert restored_container.metadata.get("files")
    restored_asset = service.get_asset_by_path(str(target_file))
    assert restored_asset is not None


def test_redo_reapplies_deletion(tmp_path):
    db_path = tmp_path / "db.sqlite"
    storage = SQLiteStorage(db_path)
    repository = AssetRepository(storage)
    service = AssetService(repository)
    history = ActionHistory(history_path=tmp_path / "history.json", trash_root=tmp_path / "trash")

    container_path = tmp_path / "container"
    container_path.mkdir()
    target_file = container_path / "note.txt"
    target_file.write_text("abc")

    container_asset = service.create_asset(
        str(container_path),
        label="Container",
        metadata={"files": [{"path": str(target_file)}]},
    )
    trash_path = history.trash_file(target_file)
    history.record_deletion(
        kind="file",
        original_path=target_file,
        trash_path=trash_path,
        container_asset_id=container_asset.id,
        container_asset_path=container_asset.path,
        container_metadata=container_asset.metadata,
        asset_snapshot={"path": str(target_file), "label": "note", "metadata": {}, "tags": []},
        asset_service=service,
    )

    # Simulate removal from metadata
    service.update_asset(container_asset.id, metadata={})

    history.undo_last(asset_service=service)
    assert target_file.exists()
    assert service.get_asset_by_path(str(target_file)) is not None
    restored_container = service.get_asset(container_asset.id)
    assert restored_container is not None
    assert restored_container.metadata.get("files")

    message = history.redo_last(asset_service=service)
    assert message is not None
    assert not target_file.exists()
    assert service.get_asset_by_path(str(target_file)) is None
    updated_container = service.get_asset(container_asset.id)
    assert updated_container is not None
    assert not updated_container.metadata.get("files")


def test_record_deletion_uses_version_system(tmp_path):
    container_root = tmp_path / "library"
    container_root.mkdir()
    db_path = tmp_path / "db.sqlite"
    storage = SQLiteStorage(db_path)
    repository = AssetRepository(storage)
    service = AssetService(repository)
    history = ActionHistory(history_path=tmp_path / "history.json", trash_root=tmp_path / "trash", max_entries=1)

    container_path = container_root / "container"
    container_path.mkdir()
    target_file = container_path / "log.txt"
    target_file.write_text("abc")

    container_asset = service.create_asset(
        str(container_path),
        label="Container",
        metadata={"files": [{"path": str(target_file)}], "notes": "snapshot"},
    )
    trash_path = history.trash_file(target_file)
    history.record_deletion(
        kind="file",
        original_path=target_file,
        trash_path=trash_path,
        container_asset_id=container_asset.id,
        container_asset_path=container_asset.path,
        container_metadata=container_asset.metadata,
        asset_snapshot={"path": str(target_file), "label": "log", "metadata": {}, "tags": []},
        asset_service=service,
    )

    visible_versions = service.list_container_versions(container_asset.id)
    assert visible_versions == []
    all_versions = service.list_all_container_versions(container_asset.id)
    assert len(all_versions) == 1
    undo_version = all_versions[0]
    assert service.is_hidden_undo_version(undo_version)
    assert undo_version.notes == UNDO_VERSION_NOTE
    assert undo_version.metadata.get("notes") == "snapshot"

    service.update_asset(container_asset.id, metadata={})
    history.undo_last(asset_service=service)

    restored_container = service.get_asset(container_asset.id)
    assert restored_container is not None
    assert restored_container.metadata.get("notes") == "snapshot"
    assert service.list_all_container_versions(container_asset.id)  # hidden version retained

