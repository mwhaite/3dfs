"""Tests for undo/redo utilities."""

from __future__ import annotations

from three_dfs.storage import AssetRepository, AssetService
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

    target_file = tmp_path / "note.txt"
    target_file.write_text("abc")
    trash_path = history.trash_file(target_file)
    history.record_deletion(
        kind="file",
        original_path=target_file,
        trash_path=trash_path,
        container_asset_id=None,
        container_asset_path=None,
        container_metadata=None,
        asset_snapshot={"path": str(target_file), "label": "note", "metadata": {}, "tags": []},
    )

    history.undo_last(asset_service=service)
    assert target_file.exists()

    message = history.redo_last(asset_service=service)
    assert message is not None
    assert not target_file.exists()

