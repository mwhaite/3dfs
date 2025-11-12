"""SQLite-backed persistence utilities for 3dfs asset metadata."""

from .database import DEFAULT_DB_PATH, SQLiteStorage
from .metadata import build_asset_metadata
from .repository import (
    AssetRecord,
    AssetRelationshipRecord,
    AssetRepository,
    ContainerVersionRecord,
    CustomizationRecord,
)
from .service import AssetSeed, AssetService

__all__ = [
    "AssetRecord",
    "AssetRelationshipRecord",
    "AssetRepository",
    "ContainerVersionRecord",
    "AssetSeed",
    "AssetService",
    "build_asset_metadata",
    "CustomizationRecord",
    "DEFAULT_DB_PATH",
    "SQLiteStorage",
]
