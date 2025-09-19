"""Tests covering the importer responsible for handling new asset files."""

from __future__ import annotations

from pathlib import Path

import pytest

from three_dfs.config import configure
from three_dfs.importer import (
    AssetImportError,
    UnsupportedAssetTypeError,
    import_asset,
)
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage


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

    return Path(__file__).parent / "fixtures" / "sample_mesh.stl"


@pytest.fixture()
def sample_obj_path() -> Path:
    """Return the path to the bundled sample OBJ asset."""

    return Path(__file__).parent / "fixtures" / "sample_mesh.obj"


@pytest.fixture()
def sample_step_path() -> Path:
    """Return the path to the bundled sample STEP asset."""

    return Path(__file__).parent / "fixtures" / "sample_block.step"


def test_importer_uses_configured_library_root(
    asset_service: AssetService,
    sample_stl_path: Path,
    tmp_path: Path,
) -> None:
    """Importer should default to the configured library location."""

    library_root = tmp_path / "library"
    configure(library_root=library_root)

    record = import_asset(sample_stl_path, service=asset_service)

    managed_path = Path(record.metadata["managed_path"])
    assert managed_path.parent == library_root
    assert Path(record.path) == managed_path


def test_importer_registers_supported_asset(
    asset_service: AssetService,
    managed_storage_root: Path,
    sample_stl_path: Path,
) -> None:
    """Importer should copy files into managed storage and register metadata."""

    record = import_asset(
        sample_stl_path,
        service=asset_service,
        storage_root=managed_storage_root,
    )

    assert record.id > 0
    assert Path(record.path).exists()

    managed_path = Path(record.metadata["managed_path"])
    assert managed_path.exists()
    assert managed_path.parent == managed_storage_root
    assert managed_path.read_text().strip().startswith("solid")

    fetched = asset_service.get_asset_by_path(record.path)
    assert fetched is not None
    assert fetched.path == record.path
    assert fetched.metadata["original_path"].endswith("sample_mesh.stl")

    stored_files = list(managed_storage_root.iterdir())
    assert stored_files == [managed_path]


def test_importer_extracts_metadata_from_stl(
    asset_service: AssetService,
    managed_storage_root: Path,
    sample_stl_path: Path,
) -> None:
    """Importer should populate mesh metadata for STL files."""

    record = import_asset(
        sample_stl_path,
        service=asset_service,
        storage_root=managed_storage_root,
    )

    metadata = record.metadata
    assert metadata["vertex_count"] == 3
    assert metadata["face_count"] == 1
    assert metadata["bounding_box_min"] == [0.0, 0.0, 0.0]
    assert metadata["bounding_box_max"] == [1.0, 1.0, 0.0]
    assert metadata["units"] == "unspecified"


def test_importer_extracts_metadata_from_obj(
    asset_service: AssetService,
    managed_storage_root: Path,
    sample_obj_path: Path,
) -> None:
    """Importer should populate mesh metadata for OBJ files."""

    record = import_asset(
        sample_obj_path,
        service=asset_service,
        storage_root=managed_storage_root,
    )

    metadata = record.metadata
    assert metadata["vertex_count"] == 4
    assert metadata["face_count"] == 2
    assert metadata["bounding_box_min"] == [-1.0, -1.0, 0.0]
    assert metadata["bounding_box_max"] == [1.0, 1.0, 0.0]
    assert metadata["units"] == "unspecified"


def test_importer_extracts_metadata_from_step(
    asset_service: AssetService,
    managed_storage_root: Path,
    sample_step_path: Path,
) -> None:
    """Importer should capture bounding boxes and units from STEP files."""

    record = import_asset(
        sample_step_path,
        service=asset_service,
        storage_root=managed_storage_root,
    )

    metadata = record.metadata
    assert metadata["vertex_count"] == 4
    assert metadata["bounding_box_min"] == [0.0, 0.0, 0.0]
    assert metadata["bounding_box_max"] == [2.0, 3.0, 4.0]
    assert metadata["units"] == "millimetre"


def test_importer_rejects_unsupported_extension(
    asset_service: AssetService,
    managed_storage_root: Path,
    tmp_path: Path,
) -> None:
    """Importer should raise a helpful error when encountering bad formats."""

    unsupported = tmp_path / "model.fbx"
    unsupported.write_text("dummy contents", encoding="utf-8")

    with pytest.raises(UnsupportedAssetTypeError):
        import_asset(
            unsupported,
            service=asset_service,
            storage_root=managed_storage_root,
        )

    assert not managed_storage_root.exists()


def test_importer_rejects_missing_files(
    asset_service: AssetService,
    managed_storage_root: Path,
    tmp_path: Path,
) -> None:
    """Importer should surface an informative error for missing inputs."""

    missing = tmp_path / "missing.step"

    with pytest.raises(FileNotFoundError):
        import_asset(
            missing,
            service=asset_service,
            storage_root=managed_storage_root,
        )

    # The importer should not create storage directories when it fails early.
    assert not managed_storage_root.exists()


def test_importer_rejects_directories(
    asset_service: AssetService,
    managed_storage_root: Path,
    tmp_path: Path,
) -> None:
    """Directories are not valid input sources for the importer."""

    source_dir = tmp_path / "source_dir"
    source_dir.mkdir()

    with pytest.raises(AssetImportError):
        import_asset(
            source_dir,
            service=asset_service,
            storage_root=managed_storage_root,
        )

    assert not managed_storage_root.exists()
