"""SQLite-backed persistence utilities for 3dfs asset metadata."""

from .database import DEFAULT_DB_PATH, SQLiteStorage
from .repository import (
    AssetRecord,
    AssetRelationshipRecord,
    AssetRepository,
    CustomizationRecord,
)
from .service import AssetSeed, AssetService

__all__ = [
    "AssetRecord",
    "AssetRelationshipRecord",
    "AssetRepository",
    "AssetSeed",
    "AssetService",
    "CustomizationRecord",
    "DEFAULT_DB_PATH",
    "SQLiteStorage",
]
