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
            row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        columns = {
            table: {row["name"]: row for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
            for table in ("customizations", "asset_relationships", "container_versions")
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
        "backend_identifier",
        "parameter_schema",
        "parameter_values",
        "created_at",
        "updated_at",
    }
    assert columns["customizations"]["parameter_schema"]["dflt_value"] == "'{}'"
    assert columns["customizations"]["parameter_values"]["dflt_value"] == "'{}'"

    assert set(columns["asset_relationships"]) == {
        "id",
        "base_asset_id",
        "customization_id",
        "generated_asset_id",
        "relationship_type",
        "created_at",
        "updated_at",
    }

    assert set(columns["container_versions"]) == {
        "id",
        "container_asset_id",
        "name",
        "metadata",
        "notes",
        "source_version_id",
        "created_at",
    }
    assert columns["container_versions"]["metadata"]["dflt_value"] == "'{}'"


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
            row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
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


def test_asset_service_prunes_missing_assets(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    service = AssetService(repository)

    existing = tmp_path / "models" / "ship.fbx"
    existing.parent.mkdir(parents=True)
    existing.write_text("mesh-data")

    present = service.create_asset(str(existing), label="Spaceship")
    missing_absolute = service.create_asset(
        str(tmp_path / "models" / "obsolete.fbx"),
        label="Obsolete",
    )
    missing_relative = service.create_asset("relative/file.txt", label="Relative")

    pruned = service.prune_missing_assets(base_path=tmp_path)

    assert pruned == 2
    assert service.get_asset_by_path(present.path) is not None
    assert service.get_asset_by_path(missing_absolute.path) is None
    assert service.get_asset_by_path(missing_relative.path) is None


def test_repository_handles_recursing_path_strings(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)

    repository.create_asset("valid/asset.txt", label="Valid")

    class BadPath:
        def __str__(self) -> str:  # pragma: no cover - recursion guard
            return str(self)

    assert repository.get_asset_by_path(BadPath()) is None
    assert repository.delete_asset_by_path(BadPath()) is False

    service = AssetService(repository)
    assert service.tags_for_path(BadPath()) == []
    # Asset previously created remains retrievable via valid path
    assert service.get_asset_by_path("valid/asset.txt") is not None


def test_customization_crud_and_relationships(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)

    base = repository.create_asset("assets/models/ship.fbx", label="Base ship")
    derivative = repository.create_asset("assets/models/ship_variant.fbx", label="Variant ship")

    customization = repository.create_customization(
        base.id,
        backend_identifier="diffusion",
        parameter_schema={"prompt": {"type": "string"}},
        parameter_values={"prompt": "Spaceship"},
    )

    assert customization.base_asset_id == base.id
    assert customization.backend_identifier == "diffusion"
    assert customization.parameter_schema["prompt"]["type"] == "string"
    assert customization.parameter_values["prompt"] == "Spaceship"

    fetched = repository.get_customization(customization.id)
    assert fetched == customization

    listed = repository.list_customizations_for_asset(base.id)
    assert [entry.id for entry in listed] == [customization.id]

    updated = repository.update_customization(
        customization.id,
        parameter_values={"prompt": "Rocket"},
    )
    assert updated.parameter_values["prompt"] == "Rocket"
    assert updated.backend_identifier == "diffusion"

    relationship = repository.create_asset_relationship(
        customization.id,
        derivative.id,
        "variant",
    )

    assert relationship.base_asset_id == base.id
    assert relationship.customization_id == customization.id
    assert relationship.generated_asset_id == derivative.id

    by_base = repository.list_relationships_for_base_asset(base.id)
    assert [rel.id for rel in by_base] == [relationship.id]

    by_generated = repository.list_relationships_for_generated_asset(derivative.id)
    assert [rel.id for rel in by_generated] == [relationship.id]

    derivative_list = repository.list_derivatives_for_asset(base.id)
    assert [asset.id for asset in derivative_list] == [derivative.id]

    base_lookup = repository.get_base_for_derivative(derivative.id)
    assert base_lookup is not None
    assert base_lookup.id == base.id

    refreshed_relationship = repository.create_asset_relationship(
        customization.id,
        derivative.id,
        "variant",
    )
    assert refreshed_relationship.id == relationship.id

    assert repository.delete_asset_relationship(relationship.id)
    assert repository.list_relationships_for_generated_asset(derivative.id) == []


def test_customization_relationships_cascade(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)

    base = repository.create_asset("assets/models/ship.fbx", label="Base ship")
    derivative = repository.create_asset("assets/models/ship_variant.fbx", label="Variant ship")

    customization = repository.create_customization(
        base.id,
        backend_identifier="pipeline",
    )
    repository.create_asset_relationship(customization.id, derivative.id, "variant")

    assert repository.delete_customization(customization.id)
    assert repository.list_relationships_for_generated_asset(derivative.id) == []

    customization = repository.create_customization(
        base.id,
        backend_identifier="pipeline",
    )
    repository.create_asset_relationship(customization.id, derivative.id, "variant")

    repository.delete_asset(derivative.id)


def test_container_versions_crud(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)

    container = repository.create_asset(
        "containers/demo",
        label="Demo",
        metadata={"kind": "container", "components": []},
    )

    version = repository.create_container_version(
        container.id,
        name="v1.0",
        metadata={"components": [{"path": "part.stl", "label": "Part"}]},
        notes="Initial snapshot",
    )

    assert version.container_asset_id == container.id
    assert version.metadata["components"][0]["path"] == "part.stl"
    assert version.notes == "Initial snapshot"

    listed = repository.list_container_versions(container.id)
    assert [entry.id for entry in listed] == [version.id]

    renamed = repository.rename_container_version(version.id, name="Release")
    assert renamed.name == "Release"
    assert repository.get_container_version(version.id).name == "Release"

    fetched = repository.get_container_version(version.id)
    assert fetched.id == version.id

    assert repository.get_container_version_by_name(container.id, "v1.0") is None
    by_name = repository.get_container_version_by_name(container.id, "Release")
    assert by_name is not None and by_name.id == version.id

    latest = repository.get_latest_container_version(container.id)
    assert latest is not None and latest.id == version.id

    assert repository.delete_container_version(version.id)
    assert repository.list_container_versions(container.id) == []


def test_asset_service_container_version_defaults(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    service = AssetService(repository)

    metadata = {
        "kind": "container",
        "components": [
            {
                "path": "component.stl",
                "label": "Component",
            }
        ],
    }

    container = service.create_asset("containers/default", label="Default", metadata=metadata)

    created = service.create_container_version(container.id, name="snapshot-1")

    assert created.metadata["components"][0]["path"] == "component.stl"

    versions = service.get_container_versions(container.id)
    assert [entry.id for entry in versions] == [created.id]

    renamed = service.rename_container_version(created.id, name="snapshot-2")
    assert renamed.name == "snapshot-2"
    assert service.get_container_version(created.id).name == "snapshot-2"


def test_asset_service_customization_helpers(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    AssetService(repository)


def test_asset_service_builds_tag_graph(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    service = AssetService(repository)

    asset_a = service.create_asset("a", label="A")
    asset_b = service.create_asset("b", label="B")
    asset_c = service.create_asset("c", label="C")

    repository.set_tags(asset_a.id, ["alpha", "beta"])
    repository.set_tags(asset_b.id, ["alpha", "gamma"])
    repository.set_tags(asset_c.id, ["beta", "gamma"])

    graph = service.build_tag_graph(min_cooccurrence=1, max_tags=None)

    node_names = {node.name: node.count for node in graph.nodes}
    assert node_names == {"alpha": 2, "beta": 2, "gamma": 2}

    edge_lookup = {(link.source, link.target): link.weight for link in graph.links}
    assert edge_lookup[("alpha", "beta")] == 1
    assert edge_lookup[("alpha", "gamma")] == 1
    assert edge_lookup[("beta", "gamma")] == 1

    base_asset = service.create_asset("assets/models/ship.fbx", label="Base ship")

    customization = service.create_customization(
        base_asset.path,
        backend_identifier="diffusion",
        parameter_schema={"prompt": {"type": "string"}},
        parameter_values={"prompt": "Spaceship"},
    )

    assert customization.base_asset_id == base_asset.id

    listed = service.list_customizations_for_asset(base_asset.path)
    assert [entry.id for entry in listed] == [customization.id]

    updated = service.update_customization(
        customization.id,
        backend_identifier="diffusion-alt",
        parameter_values={"prompt": "Rocket"},
    )
    assert updated.backend_identifier == "diffusion-alt"
    assert updated.parameter_values["prompt"] == "Rocket"

    derivative_asset, relationship = service.record_derivative(
        customization.id,
        "assets/models/ship_variant.fbx",
        relationship_type="variant",
        label="Variant ship",
        metadata={"quality": "high"},
        tags=["variant"],
    )

    assert derivative_asset.path == "assets/models/ship_variant.fbx"
    assert "variant" in derivative_asset.tags
    assert relationship.base_asset_id == base_asset.id

    derivatives = service.list_derivatives_for_asset(base_asset.path)
    assert [asset.id for asset in derivatives] == [derivative_asset.id]

    base_lookup = service.get_base_for_derivative(derivative_asset.path)
    assert base_lookup is not None and base_lookup.id == base_asset.id

    assert service.list_customizations_for_asset("missing.asset") == []
    assert service.get_base_for_derivative("missing.asset") is None

    service.delete_asset(base_asset.id)

    assert service.get_customization(customization.id) is None
    assert service.list_derivatives_for_asset(base_asset.path) == []
    assert service.get_base_for_derivative(derivative_asset.path) is None


def test_repository_recovers_from_recursive_metadata(tmp_path: Path) -> None:
    """Ensure malformed metadata that triggers recursion is treated as empty."""

    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)

    asset = repository.create_asset("assets/models/ship.fbx", label="Base ship")

    # Craft JSON that exceeds the recursion depth limit when decoding.
    deep_payload = "[" * 2048 + "0" + "]" * 2048
    with storage.connect() as connection:
        connection.execute(
            "UPDATE assets SET metadata = ? WHERE id = ?",
            (deep_payload, asset.id),
        )

    fetched = repository.get_asset(asset.id)
    assert fetched is not None
    assert fetched.metadata == {}
