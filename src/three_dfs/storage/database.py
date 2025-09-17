"""Low level SQLite helpers for the 3dfs storage package."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

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

CREATE TABLE IF NOT EXISTS asset_tags (
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    tag TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (asset_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_asset_tags_tag ON asset_tags(tag);
"""


class SQLiteStorage:
    """Encapsulate access to the on-disk SQLite database."""

    def __init__(self, path: str | Path | None = None) -> None:
        raw_path = path or DEFAULT_DB_PATH

        if isinstance(raw_path, Path):
            raw_path = raw_path.expanduser()

        if str(raw_path) == ":memory:":
            self._database = ":memory:"
            self._path: Optional[Path] = None
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
    def path(self) -> Optional[Path]:
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
