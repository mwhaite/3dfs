"""Tests covering thumbnail generation and caching for 3D assets."""

from __future__ import annotations

from pathlib import Path

import pytest

from three_dfs.importer import import_asset
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage
from three_dfs.thumbnails import DEFAULT_THUMBNAIL_SIZE, ThumbnailCache


@pytest.fixture()
def thumbnail_cache_dir(tmp_path: Path) -> Path:
    """Return a temporary directory used for storing generated thumbnails."""

    return tmp_path / "thumbnails"


@pytest.fixture()
def asset_service(tmp_path: Path, thumbnail_cache_dir: Path) -> AssetService:
    """Provide an asset service configured with isolated storage."""

    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    cache = ThumbnailCache(thumbnail_cache_dir)
    return AssetService(repository, thumbnail_cache=cache)


@pytest.fixture()
def managed_storage_root(tmp_path: Path) -> Path:
    """Temporary root used when importing assets during tests."""

    return tmp_path / "managed_assets"


@pytest.fixture()
def sample_stl_path() -> Path:
    """Path to the bundled sample STL mesh."""

    return Path(__file__).parent / "fixtures" / "sample_mesh.stl"


@pytest.fixture()
def sample_obj_path() -> Path:
    """Path to the bundled sample OBJ mesh."""

    return Path(__file__).parent / "fixtures" / "sample_mesh.obj"


@pytest.fixture()
def sample_step_path() -> Path:
    """Path to the bundled sample STEP asset."""

    return Path(__file__).parent / "fixtures" / "sample_block.step"


@pytest.mark.parametrize(
    "fixture_name",
    ["sample_stl_path", "sample_obj_path", "sample_step_path"],
)
def test_thumbnails_generated_for_supported_meshes(
    request: pytest.FixtureRequest,
    asset_service: AssetService,
    managed_storage_root: Path,
    fixture_name: str,
) -> None:
    """Thumbnails should be generated for supported mesh formats."""

    source_path: Path = request.getfixturevalue(fixture_name)
    record = import_asset(
        source_path,
        service=asset_service,
        storage_root=managed_storage_root,
    )

    asset = asset_service.get_asset_by_path(record.path)
    assert asset is not None

    asset, result = asset_service.ensure_thumbnail(asset)

    assert result is not None
    assert result.path.exists()
    assert result.path.suffix == ".png"
    assert result.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    info = asset.metadata.get("thumbnail")
    assert isinstance(info, dict)
    assert info["path"] == result.path.as_posix()
    assert info["size"] == [DEFAULT_THUMBNAIL_SIZE[0], DEFAULT_THUMBNAIL_SIZE[1]]
    assert "source_hash" in info


def test_thumbnail_cache_reused_for_subsequent_requests(
    asset_service: AssetService,
    managed_storage_root: Path,
    sample_obj_path: Path,
) -> None:
    """A cached thumbnail should be reused when the source file is unchanged."""

    record = import_asset(
        sample_obj_path,
        service=asset_service,
        storage_root=managed_storage_root,
    )

    asset = asset_service.get_asset_by_path(record.path)
    assert asset is not None

    asset, first_result = asset_service.ensure_thumbnail(asset)
    assert first_result is not None
    assert first_result.updated is True

    first_info = dict(asset.metadata["thumbnail"])

    asset, second_result = asset_service.ensure_thumbnail(asset)
    assert second_result is not None
    assert second_result.updated is False
    assert asset.metadata["thumbnail"] == first_info
    assert second_result.path.as_posix() == first_info["path"]

    cached_files = list(first_result.path.parent.iterdir())
    assert cached_files == [first_result.path]
