"""Repository classes wrapping raw SQLite access for assets and tags."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from .database import SQLiteStorage

__all__ = [
    "AssetRecord",
    "AssetRepository",
    "CustomizationRecord",
    "AssetRelationshipRecord",
]


@dataclass(slots=True)
class AssetRecord:
    """In-memory representation of an asset persisted in SQLite."""

    id: int
    path: str
    label: str
    metadata: dict[str, Any]
    tags: list[str]
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class CustomizationRecord:
    """Represent a customization stored in the database."""

    id: int
    base_asset_id: int
    backend_identifier: str
    parameter_schema: dict[str, Any]
    parameter_values: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class AssetRelationshipRecord:
    """Describe a relationship linking a customization to a generated asset."""

    id: int
    base_asset_id: int
    customization_id: int
    generated_asset_id: int
    relationship_type: str
    created_at: datetime
    updated_at: datetime


_MISSING = object()


class AssetRepository:
    """Provide CRUD operations for :class:`AssetRecord` instances."""

    def __init__(self, storage: SQLiteStorage | None = None) -> None:
        self._storage = storage or SQLiteStorage()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def storage(self) -> SQLiteStorage:
        """Return the underlying storage engine."""

        return self._storage

    @property
    def database_path(self) -> Path | None:
        """Return the filesystem path to the SQLite database if available."""

        return self._storage.path

    # ------------------------------------------------------------------
    # Asset CRUD operations
    # ------------------------------------------------------------------
    def create_asset(
        self,
        path: str,
        *,
        label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        tags: Iterable[str] | None = None,
    ) -> AssetRecord:
        """Create a new asset record and return the hydrated entity."""

        normalized_path = self._normalize_path(path)
        now = datetime.now(UTC)
        serialized_metadata = self._serialize_metadata(metadata)
        normalized_label = label or normalized_path
        normalized_tags = self._normalize_tags(tags or [])

        with self._storage.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO assets(path, label, metadata, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    normalized_path,
                    normalized_label,
                    serialized_metadata,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            asset_id = cursor.lastrowid
            if normalized_tags:
                self._replace_tags(connection, asset_id, normalized_tags)
            row = self._fetch_asset_row(connection, asset_id)
            tags_for_asset = self._fetch_tags(connection, asset_id)

        return self._row_to_record(row, tags_for_asset)

    def ensure_asset(
        self,
        path: str,
        *,
        label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AssetRecord:
        """Return an existing asset for *path* or create a placeholder."""

        existing = self.get_asset_by_path(path)
        if existing is not None:
            return existing
        return self.create_asset(path, label=label or path, metadata=metadata)

    def get_asset(self, asset_id: int) -> AssetRecord | None:
        """Return the asset identified by *asset_id* if it exists."""

        with self._storage.connect() as connection:
            row = self._fetch_asset_row(connection, asset_id)
            if row is None:
                return None
            tags = self._fetch_tags(connection, asset_id)
        return self._row_to_record(row, tags)

    def get_asset_by_path(self, path: str) -> AssetRecord | None:
        """Return the asset identified by *path* if present."""

        normalized_path = self._normalize_path(path)
        with self._storage.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assets WHERE path = ?",
                (normalized_path,),
            ).fetchone()
            if row is None:
                return None
            tags = self._fetch_tags(connection, row["id"])
        return self._row_to_record(row, tags)

    def list_assets(self) -> list[AssetRecord]:
        """Return every stored asset ordered by path."""

        with self._storage.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assets ORDER BY path COLLATE NOCASE"
            ).fetchall()
            return self._rows_to_records(connection, rows)

    def update_asset(
        self,
        asset_id: int,
        *,
        path: str | None = None,
        label: str | None = None,
        metadata: Mapping[str, Any] | None | object = _MISSING,
        tags: Iterable[str] | None | object = _MISSING,
    ) -> AssetRecord:
        """Apply updates to an asset and return the refreshed entity."""

        updates: list[str] = []
        params: list[Any] = []

        if path is not None:
            updates.append("path = ?")
            params.append(self._normalize_path(path))
        if label is not None:
            updates.append("label = ?")
            params.append(label)
        if metadata is not _MISSING:
            updates.append("metadata = ?")
            params.append(self._serialize_metadata(metadata))

        now = datetime.now(UTC)

        with self._storage.connect() as connection:
            if updates:
                updates.append("updated_at = ?")
                params.append(now.isoformat())
                params.append(asset_id)
                connection.execute(
                    f"UPDATE assets SET {', '.join(updates)} WHERE id = ?",
                    params,
                )

            if tags is not _MISSING:
                normalized_tags = self._normalize_tags(tags or [])
                self._replace_tags(connection, asset_id, normalized_tags)
                self._prune_unused_tags(connection)
                if not updates:
                    connection.execute(
                        "UPDATE assets SET updated_at = ? WHERE id = ?",
                        (now.isoformat(), asset_id),
                    )

            row = self._fetch_asset_row(connection, asset_id)
            if row is None:
                raise KeyError(f"Asset {asset_id} does not exist")
            tags_for_asset = self._fetch_tags(connection, asset_id)

        return self._row_to_record(row, tags_for_asset)

    def delete_asset(self, asset_id: int) -> bool:
        """Delete an asset by identifier."""

        with self._storage.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM assets WHERE id = ?",
                (asset_id,),
            )
            deleted = cursor.rowcount > 0
            if deleted:
                self._prune_unused_tags(connection)
            return deleted

    def delete_asset_by_path(self, path: str) -> bool:
        """Delete an asset matching *path* if present."""

        normalized_path = self._normalize_path(path)
        with self._storage.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM assets WHERE path = ?",
                (normalized_path,),
            )
            deleted = cursor.rowcount > 0
            if deleted:
                self._prune_unused_tags(connection)
            return deleted

    # ------------------------------------------------------------------
    # Customization operations
    # ------------------------------------------------------------------
    def create_customization(
        self,
        base_asset_id: int,
        *,
        backend_identifier: str,
        parameter_schema: Mapping[str, Any] | None = None,
        parameter_values: Mapping[str, Any] | None = None,
    ) -> CustomizationRecord:
        """Persist a customization tied to *base_asset_id*."""

        normalized_backend = self._normalize_backend_identifier(backend_identifier)
        now = datetime.now(UTC)
        serialized_schema = self._serialize_metadata(parameter_schema)
        serialized_values = self._serialize_metadata(parameter_values)

        with self._storage.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO customizations(
                    base_asset_id,
                    backend_identifier,
                    parameter_schema,
                    parameter_values,
                    created_at,
                    updated_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    base_asset_id,
                    normalized_backend,
                    serialized_schema,
                    serialized_values,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            customization_id = int(cursor.lastrowid)
            row = self._fetch_customization_row(connection, customization_id)

        if row is None:  # pragma: no cover - defensive safeguard
            raise RuntimeError("Failed to create customization record")
        return self._row_to_customization_record(row)

    def get_customization(self, customization_id: int) -> CustomizationRecord | None:
        """Return the customization identified by *customization_id*."""

        with self._storage.connect() as connection:
            row = self._fetch_customization_row(connection, customization_id)
        if row is None:
            return None
        return self._row_to_customization_record(row)

    def list_customizations_for_asset(
        self, base_asset_id: int
    ) -> list[CustomizationRecord]:
        """Return all customizations associated with *base_asset_id*."""

        with self._storage.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM customizations
                WHERE base_asset_id = ?
                ORDER BY created_at ASC
                """,
                (base_asset_id,),
            ).fetchall()
        return [self._row_to_customization_record(row) for row in rows]

    def update_customization(
        self,
        customization_id: int,
        *,
        backend_identifier: str | None = None,
        parameter_schema: Mapping[str, Any] | None | object = _MISSING,
        parameter_values: Mapping[str, Any] | None | object = _MISSING,
    ) -> CustomizationRecord:
        """Apply updates to a customization record."""

        updates: list[str] = []
        params: list[Any] = []

        if backend_identifier is not None:
            updates.append("backend_identifier = ?")
            params.append(self._normalize_backend_identifier(backend_identifier))
        if parameter_schema is not _MISSING:
            updates.append("parameter_schema = ?")
            params.append(self._serialize_metadata(parameter_schema))
        if parameter_values is not _MISSING:
            updates.append("parameter_values = ?")
            params.append(self._serialize_metadata(parameter_values))

        now = datetime.now(UTC)

        with self._storage.connect() as connection:
            if updates:
                updates.append("updated_at = ?")
                params.append(now.isoformat())
                params.append(customization_id)
                connection.execute(
                    f"UPDATE customizations SET {', '.join(updates)} WHERE id = ?",
                    params,
                )

            row = self._fetch_customization_row(connection, customization_id)
            if row is None:
                raise KeyError(f"Customization {customization_id} does not exist")

        return self._row_to_customization_record(row)

    def delete_customization(self, customization_id: int) -> bool:
        """Remove the customization identified by *customization_id*."""

        with self._storage.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM customizations WHERE id = ?",
                (customization_id,),
            )
            return cursor.rowcount > 0

    def create_asset_relationship(
        self,
        customization_id: int,
        generated_asset_id: int,
        relationship_type: str,
    ) -> AssetRelationshipRecord:
        """Create or refresh a relationship between customization and asset."""

        normalized_type = self._normalize_relationship_type(relationship_type)
        now = datetime.now(UTC)

        with self._storage.connect() as connection:
            customization_row = self._fetch_customization_row(
                connection,
                customization_id,
            )
            if customization_row is None:
                raise KeyError(f"Customization {customization_id} does not exist")
            base_asset_id = int(customization_row["base_asset_id"])

            connection.execute(
                """
                INSERT INTO asset_relationships(
                    base_asset_id,
                    customization_id,
                    generated_asset_id,
                    relationship_type,
                    created_at,
                    updated_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(customization_id, generated_asset_id, relationship_type)
                DO UPDATE SET
                    base_asset_id = excluded.base_asset_id,
                    updated_at = excluded.updated_at
                """,
                (
                    base_asset_id,
                    customization_id,
                    generated_asset_id,
                    normalized_type,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )

            row = connection.execute(
                """
                SELECT * FROM asset_relationships
                WHERE customization_id = ?
                  AND generated_asset_id = ?
                  AND relationship_type = ?
                """,
                (customization_id, generated_asset_id, normalized_type),
            ).fetchone()

        if row is None:  # pragma: no cover - defensive safeguard
            raise RuntimeError("Failed to create asset relationship")
        return self._row_to_relationship_record(row)

    def list_relationships_for_base_asset(
        self,
        base_asset_id: int,
        *,
        relationship_type: str | None = None,
    ) -> list[AssetRelationshipRecord]:
        """Return relationship records for a given base asset."""

        query = "SELECT * FROM asset_relationships WHERE base_asset_id = ?"
        params: list[Any] = [base_asset_id]
        if relationship_type is not None:
            query += " AND relationship_type = ?"
            params.append(self._normalize_relationship_type(relationship_type))
        query += " ORDER BY created_at ASC"

        with self._storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()

        return [self._row_to_relationship_record(row) for row in rows]

    def list_relationships_for_generated_asset(
        self,
        generated_asset_id: int,
        *,
        relationship_type: str | None = None,
    ) -> list[AssetRelationshipRecord]:
        """Return relationship records that reference *generated_asset_id*."""

        query = "SELECT * FROM asset_relationships WHERE generated_asset_id = ?"
        params: list[Any] = [generated_asset_id]
        if relationship_type is not None:
            query += " AND relationship_type = ?"
            params.append(self._normalize_relationship_type(relationship_type))
        query += " ORDER BY created_at ASC"

        with self._storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()

        return [self._row_to_relationship_record(row) for row in rows]

    def delete_asset_relationship(self, relationship_id: int) -> bool:
        """Delete a specific asset relationship by identifier."""

        with self._storage.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM asset_relationships WHERE id = ?",
                (relationship_id,),
            )
            return cursor.rowcount > 0

    def list_derivatives_for_asset(
        self,
        base_asset_id: int,
        *,
        relationship_type: str | None = None,
    ) -> list[AssetRecord]:
        """Return derivative assets produced from *base_asset_id*."""

        query = """
            SELECT assets.*
            FROM asset_relationships
            JOIN assets ON assets.id = asset_relationships.generated_asset_id
            WHERE asset_relationships.base_asset_id = ?
        """
        params: list[Any] = [base_asset_id]
        if relationship_type is not None:
            query += " AND asset_relationships.relationship_type = ?"
            params.append(self._normalize_relationship_type(relationship_type))
        query += " ORDER BY assets.path COLLATE NOCASE"

        with self._storage.connect() as connection:
            rows = connection.execute(query, params).fetchall()
            return self._rows_to_records(connection, rows)

    def get_base_for_derivative(
        self,
        generated_asset_id: int,
        *,
        relationship_type: str | None = None,
    ) -> AssetRecord | None:
        """Return the originating asset for a generated derivative."""

        query = """
            SELECT assets.*
            FROM asset_relationships
            JOIN assets ON assets.id = asset_relationships.base_asset_id
            WHERE asset_relationships.generated_asset_id = ?
        """
        params: list[Any] = [generated_asset_id]
        if relationship_type is not None:
            query += " AND asset_relationships.relationship_type = ?"
            params.append(self._normalize_relationship_type(relationship_type))
        query += " ORDER BY asset_relationships.updated_at DESC LIMIT 1"

        with self._storage.connect() as connection:
            row = connection.execute(query, params).fetchone()
            if row is None:
                return None
            tags = self._fetch_tags(connection, row["id"])
        return self._row_to_record(row, tags)

    # ------------------------------------------------------------------
    # Tag operations
    # ------------------------------------------------------------------
    def set_tags(self, asset_id: int, tags: Iterable[str]) -> list[str]:
        """Replace all tags for *asset_id* with *tags*."""

        normalized_tags = self._normalize_tags(tags)
        now = datetime.now(UTC)

        with self._storage.connect() as connection:
            self._replace_tags(connection, asset_id, normalized_tags)
            connection.execute(
                "UPDATE assets SET updated_at = ? WHERE id = ?",
                (now.isoformat(), asset_id),
            )
            self._prune_unused_tags(connection)
            return list(normalized_tags)

    def add_tag(self, asset_id: int, tag: str) -> str | None:
        """Add *tag* to *asset_id* returning the normalized value."""

        normalized_tag = self._normalize_tag(tag)
        now = datetime.now(UTC)

        with self._storage.connect() as connection:
            tag_id = self._ensure_tag_id(connection, normalized_tag)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO asset_tag_links(asset_id, tag_id)
                VALUES(?, ?)
                """,
                (asset_id, tag_id),
            )
            if cursor.rowcount:
                connection.execute(
                    "UPDATE assets SET updated_at = ? WHERE id = ?",
                    (now.isoformat(), asset_id),
                )
                return normalized_tag
            return None

    def remove_tag(self, asset_id: int, tag: str) -> bool:
        """Remove *tag* from the asset if present."""

        normalized_tag = self._normalize_tag(tag)

        with self._storage.connect() as connection:
            tag_id = self._tag_id_for_name(connection, normalized_tag)
            if tag_id is None:
                return False
            cursor = connection.execute(
                "DELETE FROM asset_tag_links WHERE asset_id = ? AND tag_id = ?",
                (asset_id, tag_id),
            )
            if cursor.rowcount:
                connection.execute(
                    "UPDATE assets SET updated_at = ? WHERE id = ?",
                    (datetime.now(UTC).isoformat(), asset_id),
                )
                self._prune_unused_tags(connection)
                return True
            return False

    def rename_tag(self, asset_id: int, old_tag: str, new_tag: str) -> str | None:
        """Rename *old_tag* to *new_tag* for the given asset."""

        normalized_old = self._normalize_tag(old_tag)
        normalized_new = self._normalize_tag(new_tag)

        with self._storage.connect() as connection:
            old_tag_id = self._tag_id_for_name(connection, normalized_old)
            if old_tag_id is None:
                return None

            link_exists = connection.execute(
                """
                SELECT 1 FROM asset_tag_links
                WHERE asset_id = ? AND tag_id = ?
                """,
                (asset_id, old_tag_id),
            ).fetchone()
            if link_exists is None:
                return None

            new_tag_id = self._ensure_tag_id(connection, normalized_new)
            collision = connection.execute(
                """
                SELECT 1 FROM asset_tag_links WHERE asset_id = ? AND tag_id = ?
                """,
                (asset_id, new_tag_id),
            ).fetchone()
            if collision and new_tag_id != old_tag_id:
                return None

            connection.execute(
                """
                UPDATE asset_tag_links SET tag_id = ?
                WHERE asset_id = ? AND tag_id = ?
                """,
                (new_tag_id, asset_id, old_tag_id),
            )
            connection.execute(
                "UPDATE assets SET updated_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), asset_id),
            )
            self._prune_unused_tags(connection)
            return normalized_new

    def tags_for_path(self, path: str) -> list[str]:
        """Return the tags associated with *path*."""

        asset = self.get_asset_by_path(path)
        if asset is None:
            return []
        return list(asset.tags)

    def search_tags(self, query: str) -> dict[str, list[str]]:
        """Return assets whose tag text contains *query* (case-insensitive)."""

        needle = str(query or "").strip().casefold()
        results: dict[str, list[str]] = {}

        with self._storage.connect() as connection:
            rows = connection.execute(
                """
                SELECT assets.path AS path, tags.name AS tag
                FROM assets
                JOIN asset_tag_links ON asset_tag_links.asset_id = assets.id
                JOIN tags ON tags.id = asset_tag_links.tag_id
                ORDER BY assets.path COLLATE NOCASE, tags.name COLLATE NOCASE
                """
            ).fetchall()

        for row in rows:
            tag = str(row["tag"])
            if needle and needle not in tag.casefold():
                continue
            results.setdefault(str(row["path"]), []).append(tag)

        return results

    def all_tags(self) -> list[str]:
        """Return every tag stored across all assets."""

        with self._storage.connect() as connection:
            rows = connection.execute(
                """
                SELECT name FROM tags
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()
            return [str(row["name"]) for row in rows]

    def iter_tagged_assets(self) -> Iterator[tuple[str, list[str]]]:
        """Yield ``(path, tags)`` pairs for assets with assigned tags."""

        with self._storage.connect() as connection:
            rows = connection.execute(
                """
                SELECT assets.path AS path, tags.name AS tag
                FROM assets
                JOIN asset_tag_links ON asset_tag_links.asset_id = assets.id
                JOIN tags ON tags.id = asset_tag_links.tag_id
                ORDER BY assets.path COLLATE NOCASE, tags.name COLLATE NOCASE
                """
            ).fetchall()

        current_path: str | None = None
        bucket: list[str] = []

        for row in rows:
            path = str(row["path"])
            tag = str(row["tag"])
            if current_path is None:
                current_path = path
            if path != current_path:
                yield current_path, list(bucket)
                current_path = path
                bucket = [tag]
            else:
                bucket.append(tag)

        if current_path is not None:
            yield current_path, list(bucket)

    def _normalize_path(self, path: str) -> str:
        value = str(path).strip()
        if not value:
            raise ValueError("Asset path cannot be empty")
        return value

    def _normalize_backend_identifier(self, backend: str) -> str:
        value = str(backend).strip()
        if not value:
            raise ValueError("Backend identifier cannot be empty")
        return value

    def _normalize_relationship_type(self, relationship_type: str) -> str:
        value = str(relationship_type).strip()
        if not value:
            raise ValueError("Relationship type cannot be empty")
        return value

    def _normalize_tag(self, tag: str) -> str:
        value = str(tag).strip()
        if not value:
            raise ValueError("Tags must contain visible characters")
        return value

    def _normalize_tags(self, tags: Iterable[str]) -> list[str]:
        seen: dict[str, None] = {}
        for tag in tags:
            normalized = self._normalize_tag(tag)
            seen.setdefault(normalized, None)
        return sorted(seen, key=str.casefold)

    def _serialize_metadata(self, metadata: Mapping[str, Any] | None | object) -> str:
        if metadata is None or metadata is _MISSING:
            return json.dumps({})
        return json.dumps(dict(metadata))

    def _fetch_asset_row(self, connection: Connection, asset_id: int):
        return connection.execute(
            "SELECT * FROM assets WHERE id = ?",
            (asset_id,),
        ).fetchone()

    def _fetch_customization_row(self, connection: Connection, customization_id: int):
        return connection.execute(
            "SELECT * FROM customizations WHERE id = ?",
            (customization_id,),
        ).fetchone()

    def _fetch_tags(self, connection: Connection, asset_id: int) -> list[str]:
        rows = connection.execute(
            """
            SELECT tags.name AS tag
            FROM asset_tag_links
            JOIN tags ON tags.id = asset_tag_links.tag_id
            WHERE asset_tag_links.asset_id = ?
            ORDER BY tags.name COLLATE NOCASE
            """,
            (asset_id,),
        ).fetchall()
        return [str(row["tag"]) for row in rows]

    def _fetch_asset_relationship_row(
        self, connection: Connection, relationship_id: int
    ):
        return connection.execute(
            "SELECT * FROM asset_relationships WHERE id = ?",
            (relationship_id,),
        ).fetchone()

    def _rows_to_records(self, connection: Connection, rows) -> list[AssetRecord]:
        if not rows:
            return []
        asset_ids = [row["id"] for row in rows]
        tags_map = self._tags_for_asset_ids(connection, asset_ids)
        return [self._row_to_record(row, tags_map.get(row["id"], [])) for row in rows]

    def _tags_for_asset_ids(
        self, connection: Connection, asset_ids: list[int]
    ) -> dict[int, list[str]]:
        if not asset_ids:
            return {}
        placeholders = ",".join(["?"] * len(asset_ids))
        rows = connection.execute(
            f"""
            SELECT asset_tag_links.asset_id AS asset_id, tags.name AS tag
            FROM asset_tag_links
            JOIN tags ON tags.id = asset_tag_links.tag_id
            WHERE asset_id IN ({placeholders})
            ORDER BY asset_tag_links.asset_id, tags.name COLLATE NOCASE
            """,
            asset_ids,
        ).fetchall()
        tags_map: dict[int, list[str]] = {asset_id: [] for asset_id in asset_ids}
        for row in rows:
            tags_map.setdefault(row["asset_id"], []).append(str(row["tag"]))
        return tags_map

    def _row_to_record(self, row, tags: list[str]) -> AssetRecord:
        metadata = self._deserialize_metadata(row["metadata"])
        created_at = datetime.fromisoformat(row["created_at"])
        updated_at = datetime.fromisoformat(row["updated_at"])
        return AssetRecord(
            id=row["id"],
            path=str(row["path"]),
            label=str(row["label"]),
            metadata=metadata,
            tags=list(tags),
            created_at=created_at,
            updated_at=updated_at,
        )

    def _row_to_customization_record(self, row) -> CustomizationRecord:
        parameter_schema = self._deserialize_metadata(row["parameter_schema"])
        parameter_values = self._deserialize_metadata(row["parameter_values"])
        created_at = datetime.fromisoformat(row["created_at"])
        updated_at = datetime.fromisoformat(row["updated_at"])
        return CustomizationRecord(
            id=row["id"],
            base_asset_id=row["base_asset_id"],
            backend_identifier=str(row["backend_identifier"]),
            parameter_schema=parameter_schema,
            parameter_values=parameter_values,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _row_to_relationship_record(self, row) -> AssetRelationshipRecord:
        created_at = datetime.fromisoformat(row["created_at"])
        updated_at = datetime.fromisoformat(row["updated_at"])
        return AssetRelationshipRecord(
            id=row["id"],
            base_asset_id=row["base_asset_id"],
            customization_id=row["customization_id"],
            generated_asset_id=row["generated_asset_id"],
            relationship_type=str(row["relationship_type"]),
            created_at=created_at,
            updated_at=updated_at,
        )

    def _deserialize_metadata(self, payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            data = json.loads(payload)
        except (RecursionError, json.JSONDecodeError):
            return {}
        if isinstance(data, dict):
            return dict(data)
        return {}

    def _replace_tags(
        self, connection: Connection, asset_id: int, tags: list[str]
    ) -> None:
        connection.execute(
            "DELETE FROM asset_tag_links WHERE asset_id = ?",
            (asset_id,),
        )
        if not tags:
            return
        tag_ids = [self._ensure_tag_id(connection, tag) for tag in tags]
        connection.executemany(
            "INSERT OR IGNORE INTO asset_tag_links(asset_id, tag_id) VALUES(?, ?)",
            [(asset_id, tag_id) for tag_id in tag_ids],
        )

    def _ensure_tag_id(self, connection: Connection, tag: str) -> int:
        existing = connection.execute(
            "SELECT id FROM tags WHERE name = ?",
            (tag,),
        ).fetchone()
        if existing is not None:
            return int(existing["id"])
        cursor = connection.execute(
            "INSERT INTO tags(name) VALUES(?)",
            (tag,),
        )
        return int(cursor.lastrowid)

    def _tag_id_for_name(self, connection: Connection, tag: str) -> int | None:
        row = connection.execute(
            "SELECT id FROM tags WHERE name = ?",
            (tag,),
        ).fetchone()
        if row is None:
            return None
        return int(row["id"])

    def _prune_unused_tags(self, connection: Connection) -> None:
        connection.execute(
            """
            DELETE FROM tags
            WHERE id NOT IN (
                SELECT DISTINCT tag_id FROM asset_tag_links
            )
            """
        )
