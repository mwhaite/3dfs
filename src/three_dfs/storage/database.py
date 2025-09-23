"""Low level SQLite helpers for the 3dfs storage package."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_FILENAME = "assets.sqlite3"
DEFAULT_DB_PATH = Path.home() / ".3dfs" / DEFAULT_DB_FILENAME

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS asset_tag_links (
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (asset_id, tag_id)
);

CREATE TABLE IF NOT EXISTS customizations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    base_asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    backend_identifier TEXT NOT NULL,
    parameter_schema TEXT NOT NULL DEFAULT '{}',
    parameter_values TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asset_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    base_asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    customization_id INTEGER NOT NULL REFERENCES customizations(id) ON DELETE CASCADE,
    generated_asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (customization_id, generated_asset_id, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name);
CREATE INDEX IF NOT EXISTS idx_asset_tag_links_tag_id ON asset_tag_links(tag_id);
CREATE INDEX IF NOT EXISTS idx_asset_tag_links_asset_id ON asset_tag_links(asset_id);
CREATE INDEX IF NOT EXISTS idx_customizations_base_asset_id
    ON customizations(base_asset_id);
CREATE INDEX IF NOT EXISTS idx_customizations_backend_identifier
    ON customizations(backend_identifier);
CREATE INDEX IF NOT EXISTS idx_asset_relationships_base_asset_id
    ON asset_relationships(base_asset_id);
CREATE INDEX IF NOT EXISTS idx_asset_relationships_customization_id
    ON asset_relationships(customization_id);
CREATE INDEX IF NOT EXISTS idx_asset_relationships_generated_asset_id
    ON asset_relationships(generated_asset_id);
CREATE INDEX IF NOT EXISTS idx_asset_relationships_relationship_type
    ON asset_relationships(relationship_type);

-- Assemblies: collections of assets/components
CREATE TABLE IF NOT EXISTS assemblies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assembly_components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assembly_id INTEGER NOT NULL REFERENCES assemblies(id) ON DELETE CASCADE,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    quantity INTEGER NOT NULL DEFAULT 1,
    order_index INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    UNIQUE(assembly_id, asset_id, order_index)
);

CREATE INDEX IF NOT EXISTS idx_assembly_components_assembly_id
    ON assembly_components(assembly_id);
CREATE INDEX IF NOT EXISTS idx_assembly_components_asset_id
    ON assembly_components(asset_id);
"""


class SQLiteStorage:
    """Encapsulate access to the on-disk SQLite database."""

    def __init__(self, path: str | Path | None = None) -> None:
        raw_path = path or DEFAULT_DB_PATH

        if isinstance(raw_path, Path):
            raw_path = raw_path.expanduser()

        if str(raw_path) == ":memory:":
            self._database = ":memory:"
            self._path: Path | None = None
        else:
            actual_path = Path(raw_path).expanduser()
            actual_path.parent.mkdir(parents=True, exist_ok=True)
            self._database = str(actual_path)
            self._path = actual_path

        self._initialize_schema()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def path(self) -> Path | None:
        """Return the filesystem location for the database if persisted."""

        return self._path

    def connect(self) -> sqlite3.Connection:
        """Return a new SQLite connection with required pragmas applied."""

        connection = sqlite3.connect(self._database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _initialize_schema(self) -> None:
        """Ensure the schema is created for the target database."""

        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_legacy_tags(connection)

    def _migrate_legacy_tags(self, connection: sqlite3.Connection) -> None:
        """Upgrade databases created prior to the dedicated tag tables.

        The original schema stored tags directly in an ``asset_tags`` table with
        ``(asset_id, tag)`` pairs.  The current schema normalizes tag names into
        a dedicated ``tags`` table and tracks many-to-many relationships via
        ``asset_tag_links``.  When the legacy table is detected the data is
        copied into the new structures and the outdated table is removed.
        """

        legacy_table = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'asset_tags'
            """
        ).fetchone()
        if legacy_table is None:
            return

        column_info = connection.execute("PRAGMA table_info(asset_tags)").fetchall()
        column_names = {row["name"] for row in column_info}
        if "tag" not in column_names:
            # The database already uses the new schema.
            return

        rows = connection.execute("SELECT asset_id, tag FROM asset_tags").fetchall()

        def ensure_tag_id(tag_name: str) -> int:
            existing = connection.execute(
                "SELECT id FROM tags WHERE name = ?",
                (tag_name,),
            ).fetchone()
            if existing is not None:
                return int(existing["id"])
            cursor = connection.execute(
                "INSERT INTO tags(name) VALUES(?)",
                (tag_name,),
            )
            return int(cursor.lastrowid)

        for row in rows:
            tag_value = (row["tag"] or "").strip()
            if not tag_value:
                continue
            tag_id = ensure_tag_id(tag_value)
            connection.execute(
                "INSERT OR IGNORE INTO asset_tag_links(asset_id, tag_id) VALUES(?, ?)",
                (row["asset_id"], tag_id),
            )

        connection.execute("DROP TABLE asset_tags")
