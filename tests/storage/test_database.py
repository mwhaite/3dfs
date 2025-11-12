"""Tests for the low-level SQLite helpers."""

from __future__ import annotations

import sqlite3

import pytest

from three_dfs.storage import database


class DummyError(RuntimeError):
    """Raised when the monkeypatched connect function is called."""


def test_connect_uses_original_sqlite3(monkeypatch: pytest.MonkeyPatch) -> None:
    """``SQLiteStorage.connect`` should ignore later sqlite3 monkeypatches."""

    storage = database.SQLiteStorage(":memory:")

    def exploding_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise DummyError("patched sqlite3.connect should not be called")

    monkeypatch.setattr(database.sqlite3, "connect", exploding_connect)

    connection = storage.connect()
    try:
        assert isinstance(connection, sqlite3.Connection)
    finally:
        connection.close()
