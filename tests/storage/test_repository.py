"""Tests covering the SQLite-backed asset repository and service."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
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
        columns = {
            table: {
                row["name"]: row
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for table in ("customizations", "asset_relationships")
        }

    assert {
        "assets",
        "tags",
        "asset_tag_links",
        "customizations",
        "asset_relationships",
    }.issubset(tables)
    assert "asset_tags" not in tables

    assert set(columns["customizations"]) == {
        "id",
        "base_asset_id",
        "parameters",
        "created_at",
        "updated_at",
    }
    assert columns["customizations"]["parameters"]["dflt_value"] == "'{}'"

    assert set(columns["asset_relationships"]) == {
        "customization_id",
        "generated_asset_id",
        "relationship_type",
        "created_at",
        "updated_at",
    }


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
    assert repository.all_tags() == ["model", "vehicle"]

    fetched = repository.get_asset_by_path("assets/models/ship.fbx")
    assert fetched is not None
    assert fetched.metadata["description"] == "Spacecraft for testing"

    repository.add_tag(created.id, "Featured")
    repository.remove_tag(created.id, "model")

    refreshed = repository.get_asset(created.id)
    assert refreshed is not None
    assert refreshed.tags == ["Featured", "vehicle"]
    assert repository.all_tags() == ["Featured", "vehicle"]

    repository.set_tags(created.id, ["alpha", "beta"])
    updated = repository.get_asset(created.id)
    assert updated is not None
    assert updated.tags == ["alpha", "beta"]
    assert repository.all_tags() == ["alpha", "beta"]

    listing = repository.list_assets()
    assert [asset.path for asset in listing] == ["assets/models/ship.fbx"]

    search_results = repository.search_tags("alp")
    assert search_results == {"assets/models/ship.fbx": ["alpha"]}


def test_sqlite_storage_migrates_legacy_tag_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    now = datetime.now(UTC).isoformat()

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE asset_tags (
                asset_id INTEGER NOT NULL,
                tag TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO assets(id, path, label, metadata, created_at, updated_at)
            VALUES(1, ?, ?, ?, ?, ?)
            """,
            ("docs/legacy.txt", "Legacy", "{}", now, now),
        )
        connection.executemany(
            "INSERT INTO asset_tags(asset_id, tag) VALUES(?, ?)",
            [(1, "alpha"), (1, "beta")],
        )

    storage = SQLiteStorage(db_path)
    repository = AssetRepository(storage)

    asset = repository.get_asset(1)
    assert asset is not None
    assert asset.tags == ["alpha", "beta"]
    assert repository.all_tags() == ["alpha", "beta"]

    with storage.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert {
        "assets",
        "tags",
        "asset_tag_links",
        "customizations",
        "asset_relationships",
    }.issubset(tables)
    assert "asset_tags" not in tables


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
