"""Tests covering the SQLite-backed asset repository and service."""

from __future__ import annotations

from pathlib import Path

from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage


def test_sqlite_storage_initializes_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "assets.sqlite3"
    storage = SQLiteStorage(db_path)

    assert db_path.exists()

    with storage.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert {"assets", "asset_tags"}.issubset(tables)


def test_asset_repository_persists_records(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)

    created = repository.create_asset(
        "assets/models/ship.fbx",
        label="Spaceship",
        metadata={"description": "Spacecraft for testing"},
        tags=["vehicle", "model"],
    )

    assert created.id > 0
    assert created.tags == ["model", "vehicle"]

    fetched = repository.get_asset_by_path("assets/models/ship.fbx")
    assert fetched is not None
    assert fetched.metadata["description"] == "Spacecraft for testing"

    repository.add_tag(created.id, "Featured")
    repository.remove_tag(created.id, "model")

    refreshed = repository.get_asset(created.id)
    assert refreshed is not None
    assert refreshed.tags == ["Featured", "vehicle"]

    repository.set_tags(created.id, ["alpha", "beta"])
    updated = repository.get_asset(created.id)
    assert updated is not None
    assert updated.tags == ["alpha", "beta"]

    listing = repository.list_assets()
    assert [asset.path for asset in listing] == ["assets/models/ship.fbx"]

    search_results = repository.search_tags("alp")
    assert search_results == {"assets/models/ship.fbx": ["alpha"]}


def test_asset_service_bootstrap_demo_data(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    service = AssetService(repository)

    assets = service.bootstrap_demo_data()
    assert len(assets) >= 1

    # Bootstrapping again should not duplicate entries.
    second_run = service.bootstrap_demo_data()
    assert [asset.path for asset in assets] == [asset.path for asset in second_run]

    # At least one asset should expose tags via the service.
    tagged = {path: tags for path, tags in service.iter_tagged_assets()}
    assert tagged
