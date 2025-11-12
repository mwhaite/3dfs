"""Tests covering the import plugin infrastructure."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from three_dfs.import_plugins import (
    iter_plugins,
    register_plugin,
    scaffold_plugin,
    unregister_plugin,
)
from three_dfs.importer import import_asset
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage


class DummyPlugin:
    """Simple plugin used to exercise the registry and importer."""

    def __init__(self, fixture: Path) -> None:
        self.fixture = fixture

    def can_handle(self, source: str) -> bool:
        return source.startswith("dummy://")

    def fetch(self, source: str, destination: Path) -> dict[str, object]:
        shutil.copy2(self.fixture, destination)
        return {"remote_source": source, "dummy": True}


@pytest.fixture()
def sample_stl_path() -> Path:
    """Return the path to the bundled STL sample."""

    return Path(__file__).resolve().parent.parent / "fixtures" / "sample_mesh.stl"


@pytest.fixture()
def asset_service(tmp_path: Path) -> AssetService:
    """Provide an asset service backed by a temporary database."""

    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    return AssetService(repository)


@pytest.fixture()
def managed_storage_root(tmp_path: Path) -> Path:
    """Return the directory used for managed copies during tests."""

    return tmp_path / "managed_assets"


@pytest.fixture()
def dummy_plugin(sample_stl_path: Path) -> DummyPlugin:
    """Register the dummy plugin for the duration of a test."""

    plugin = DummyPlugin(sample_stl_path)
    register_plugin(plugin)
    try:
        yield plugin
    finally:
        unregister_plugin(plugin)


def test_registry_detects_registered_plugin(dummy_plugin: DummyPlugin) -> None:
    """Plugins registered at runtime should be visible via the iterator."""

    assert dummy_plugin in iter_plugins()


def test_importer_uses_remote_plugin(
    dummy_plugin: DummyPlugin,
    asset_service: AssetService,
    managed_storage_root: Path,
) -> None:
    """Importer should leverage plugins for remote sources and keep metadata."""

    remote_identifier = "dummy://assets/sample_mesh.stl"
    record = import_asset(
        remote_identifier,
        service=asset_service,
        storage_root=managed_storage_root,
    )

    metadata = record.metadata
    assert metadata["remote_source"] == remote_identifier
    assert metadata["source_type"] == "remote"
    assert metadata["dummy"] is True
    assert Path(metadata["managed_path"]).exists()
    assert record.path == metadata["managed_path"]


def test_scaffold_plugin_emits_template(tmp_path: Path) -> None:
    """The scaffold helper should generate a plugin stub with TODO hooks."""

    module_path = scaffold_plugin("Example Repo", tmp_path)
    contents = module_path.read_text(encoding="utf-8")

    assert module_path.exists()
    assert "class ExampleRepoImportPlugin" in contents
    assert "TODO: Handle authentication" in contents
    assert "TODO: Scrape or download" in contents
    assert "TODO: Map remote metadata" in contents
