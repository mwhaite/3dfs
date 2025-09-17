"""Tests for the tag persistence layer."""

from __future__ import annotations

from pathlib import Path

from three_dfs.data.tags import TagStore


def test_tag_store_add_remove_rename(tmp_path: Path) -> None:
    store_path = tmp_path / "tags.json"
    store = TagStore(store_path)

    assert store.tags_for("item-1") == []

    added = store.add_tag("item-1", "First")
    assert added == "First"
    assert store.tags_for("item-1") == ["First"]

    # Duplicate addition returns None but does not raise.
    assert store.add_tag("item-1", "First") is None

    # Renaming the tag should persist.
    renamed = store.rename_tag("item-1", "First", "Renamed")
    assert renamed == "Renamed"
    assert store.tags_for("item-1") == ["Renamed"]

    # Removing the tag clears the item entry entirely.
    removed = store.remove_tag("item-1", "Renamed")
    assert removed is True
    assert store.tags_for("item-1") == []


def test_tag_store_persistence_roundtrip(tmp_path: Path) -> None:
    store_path = tmp_path / "tags.json"
    store = TagStore(store_path)

    store.add_tag("foo", "Bar")
    store.add_tag("foo", "Baz")
    store.add_tag("qux", "Bar")

    # A new instance pointing to the same file should read the state back.
    reloaded = TagStore(store_path)
    assert reloaded.tags_for("foo") == ["Bar", "Baz"]
    assert reloaded.search("bar") == {"foo": ["Bar"], "qux": ["Bar"]}
    assert set(reloaded.all_tags()) == {"Bar", "Baz"}
