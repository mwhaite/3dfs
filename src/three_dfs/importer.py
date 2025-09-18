"""Utilities for importing external 3D assets into managed storage."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from .storage import AssetService

if TYPE_CHECKING:
    from .storage import AssetRecord

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "AssetImportError",
    "UnsupportedAssetTypeError",
    "import_asset",
]

SUPPORTED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".stl",
        ".obj",
        ".step",
        ".stp",
    }
)
"""Supported file extensions for imported assets."""

DEFAULT_STORAGE_ROOT: Final[Path] = Path.home() / ".3dfs" / "assets" / "imports"
"""Default directory where imported assets are stored."""


class AssetImportError(RuntimeError):
    """Base exception raised when an asset cannot be imported."""


class UnsupportedAssetTypeError(AssetImportError):
    """Raised when attempting to import an unsupported asset format."""


def import_asset(
    path: Path,
    *,
    service: AssetService | None = None,
    storage_root: Path | None = None,
) -> AssetRecord:
    """Import the asset located at *path* into managed storage.

    Parameters
    ----------
    path:
        The filesystem path to the asset to import.
    service:
        Optional :class:`~three_dfs.storage.AssetService` used to register the
        asset. A new service instance is created when omitted.
    storage_root:
        Directory where managed copies of imported assets are persisted. When
        omitted the importer uses :data:`DEFAULT_STORAGE_ROOT`.

    Returns
    -------
    AssetRecord
        The newly registered asset record.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist on disk.
    AssetImportError
        If *path* is not a file or has an unsupported extension.
    """

    source = Path(path).expanduser()
    try:
        source = source.resolve(strict=True)
    except FileNotFoundError as exc:  # pragma: no cover - value re-raised unchanged
        raise FileNotFoundError(f"Asset {path!s} does not exist") from exc

    if not source.is_file():
        raise AssetImportError(f"Asset {source!s} is not a file")

    extension = source.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedAssetTypeError(
            f"Unsupported asset format '{extension or 'unknown'}'"
        )

    managed_root = Path(storage_root or DEFAULT_STORAGE_ROOT).expanduser()
    managed_root.mkdir(parents=True, exist_ok=True)
    destination = _allocate_destination(managed_root, source.name)

    shutil.copy2(source, destination)
    imported_at = datetime.now(UTC).isoformat()
    metadata = {
        "original_path": str(source),
        "managed_path": str(destination),
        "extension": extension.lstrip(".").upper(),
        "size": destination.stat().st_size,
        "imported_at": imported_at,
    }

    asset_service = service or AssetService()
    try:
        record = asset_service.create_asset(
            destination.as_posix(),
            label=source.stem,
            metadata=metadata,
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return record


def _allocate_destination(storage_root: Path, filename: str) -> Path:
    """Return a non-conflicting destination for *filename* within *storage_root*."""

    candidate = storage_root / filename
    if not candidate.exists():
        return candidate

    original = Path(filename)
    stem = original.stem
    suffix = original.suffix
    counter = 1

    while True:
        candidate = storage_root / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
