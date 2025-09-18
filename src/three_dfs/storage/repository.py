"""Repository classes wrapping raw SQLite access for assets and tags."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection
from typing import Any, Iterable, Iterator, Mapping

from .database import SQLiteStorage

__all__ = ["AssetRecord", "AssetRepository"]


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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _normalize_path(self, path: str) -> str:
        value = str(path).strip()
        if not value:
            raise ValueError("Asset path cannot be empty")
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

    def _rows_to_records(self, connection: Connection, rows) -> list[AssetRecord]:
        if not rows:
            return []
        asset_ids = [row["id"] for row in rows]
        tags_map = self._tags_for_asset_ids(connection, asset_ids)
        return [
            self._row_to_record(row, tags_map.get(row["id"], []))
            for row in rows
        ]

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

    def _deserialize_metadata(self, payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        if isinstance(data, dict):
            return dict(data)
        return {}

    def _replace_tags(self, connection: Connection, asset_id: int, tags: list[str]) -> None:
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
