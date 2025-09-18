"""Tests covering the import plugin infrastructure."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from three_dfs.import_plugins import (
    clear_plugins,
    register_plugin,
    scaffold_plugin,
)
from three_dfs.importer import import_asset
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage


@pytest.fixture(autouse=True)
def reset_plugins() -> None:
    """Ensure the plugin registry is clean for each test."""

    clear_plugins()
    yield
    clear_plugins()


@pytest.fixture()
def asset_service(tmp_path: Path) -> AssetService:
    """Provide an asset service backed by a temporary SQLite database."""

    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    return AssetService(repository)


@pytest.fixture()
def managed_storage_root(tmp_path: Path) -> Path:
    """Return the root directory used for managed asset copies during tests."""

    return tmp_path / "managed_assets"


@pytest.fixture()
def sample_stl_path() -> Path:
    """Return the path to the bundled sample STL asset."""

    return Path(__file__).resolve().parent.parent / "fixtures" / "sample_mesh.stl"


class DummyRemotePlugin:
    """Simple plugin used to validate the import pipeline."""

    def __init__(self, fixture_path: Path) -> None:
        self._fixture_path = fixture_path

    def can_handle(self, source: str) -> bool:
        return source.startswith("dummy://")

    def fetch(self, source: str, destination: Path) -> dict[str, object]:
        shutil.copy2(self._fixture_path, destination)
        return {
            "extension": "stl",
            "remote_url": source,
            "label": "Dummy Remote Mesh",
        }


def test_remote_plugin_imports_asset(
    asset_service: AssetService,
    managed_storage_root: Path,
    sample_stl_path: Path,
) -> None:
    """Importer should use registered plugins to download remote assets."""

    register_plugin(DummyRemotePlugin(sample_stl_path))

    source = "dummy://assets/sample"
    record = import_asset(
        source,
        service=asset_service,
        storage_root=managed_storage_root,
    )

    managed_path = Path(record.metadata["managed_path"])
    assert managed_path.exists()
    assert managed_path.suffix == ".stl"
    assert Path(record.path) == managed_path

    metadata = record.metadata
    assert metadata["remote_url"] == source
    assert metadata["remote_source"] == source
    assert metadata["original_path"] == source
    assert metadata["source"] == source
    assert metadata["import_plugin"].endswith("DummyRemotePlugin")
    assert record.label == "Dummy Remote Mesh"


def test_scaffold_plugin_emits_template(tmp_path: Path) -> None:
    """The scaffold helper should create a plugin skeleton with TODO hooks."""

    destination = scaffold_plugin("Example Repo", tmp_path)
    contents = destination.read_text(encoding="utf-8")

    assert destination.name == "example_repo_plugin.py"
    assert "class ExampleRepoPlugin" in contents
    assert "register_plugin" in contents
    assert "TODO: Implement authentication" in contents
