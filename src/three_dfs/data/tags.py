"""Tag persistence helpers.

This module provides a light-weight persistence layer for associating text
labels ("tags") with arbitrary repository item identifiers.  The
implementation is intentionally simple – a JSON file on disk – but exposes a
clean API that higher level UI components can rely on.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path

__all__ = ["TagStore"]

DEFAULT_DATA_DIR = Path.home() / ".3dfs"
DEFAULT_DATA_FILE = DEFAULT_DATA_DIR / "tags.json"


class TagStore:
    """Persist and query tag assignments for repository items.

    Parameters
    ----------
    path:
        Optional location for the backing JSON file.  When omitted a file in
        the user's home directory (``~/.3dfs/tags.json``) is used.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_DATA_FILE
        self._records: dict[str, list[str]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Basic helpers
    # ------------------------------------------------------------------
    @property
    def path(self) -> Path:
        """Return the resolved location of the persistence file."""

        return self._path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def tags_for(self, item_id: str) -> list[str]:
        """Return a copy of the tags assigned to *item_id*.

        The returned list is always sorted alphabetically (case-insensitive)
        and can be mutated by the caller without affecting the stored state.
        """

        item_key = self._normalize_item_id(item_id)
        tags = self._records.get(item_key, [])
        return list(tags)

    def set_tags(self, item_id: str, tags: Iterable[str]) -> list[str]:
        """Replace the tags for *item_id* with *tags*.

        Returns the normalized, sorted tag list that was persisted.
        """

        item_key = self._normalize_item_id(item_id)
        normalized = self._normalize_tag_iterable(tags)

        if normalized:
            self._records[item_key] = normalized
        else:
            self._records.pop(item_key, None)

        self._save()
        return list(normalized)

    def add_tag(self, item_id: str, tag: str) -> str | None:
        """Add *tag* to *item_id* and return the normalized value.

        ``None`` is returned when the tag already exists for the item.
        """

        item_key = self._normalize_item_id(item_id)
        normalized = self._normalize_tag(tag)
        tags = self._records.setdefault(item_key, [])

        if normalized in tags:
            return None

        tags.append(normalized)
        tags.sort(key=str.casefold)
        self._save()
        return normalized

    def remove_tag(self, item_id: str, tag: str) -> bool:
        """Remove *tag* from *item_id* if present."""

        item_key = self._normalize_item_id(item_id)
        normalized = self._normalize_tag(tag)
        tags = self._records.get(item_key)

        if not tags or normalized not in tags:
            return False

        tags.remove(normalized)

        if tags:
            tags.sort(key=str.casefold)
        else:
            del self._records[item_key]

        self._save()
        return True

    def rename_tag(self, item_id: str, old_tag: str, new_tag: str) -> str | None:
        """Rename *old_tag* to *new_tag* for *item_id*.

        The normalized new tag name is returned on success.  ``None`` is
        returned when *old_tag* does not exist for the item or when *new_tag*
        would collide with an existing entry.
        """

        item_key = self._normalize_item_id(item_id)
        tags = self._records.get(item_key)

        if not tags:
            return None

        old_normalized = self._normalize_tag(old_tag)
        if old_normalized not in tags:
            return None

        new_normalized = self._normalize_tag(new_tag)

        if new_normalized in tags and new_normalized != old_normalized:
            return None

        index = tags.index(old_normalized)
        tags[index] = new_normalized
        tags.sort(key=str.casefold)
        self._save()
        return new_normalized

    def search(self, query: str) -> dict[str, list[str]]:
        """Return all tags whose text contains *query* (case-insensitive)."""

        needle = str(query or "").strip().casefold()

        if not needle:
            return {item_id: list(tags) for item_id, tags in self._records.items()}

        results: dict[str, list[str]] = {}

        for item_id, tags in self._records.items():
            matches = [tag for tag in tags if needle in tag.casefold()]
            if matches:
                results[item_id] = matches

        return results

    def all_tags(self) -> list[str]:
        """Return a sorted list of every tag stored for any item."""

        universe = {tag for tags in self._records.values() for tag in tags}
        return sorted(universe, key=str.casefold)

    def iter_items(self) -> Iterator[tuple[str, list[str]]]:
        """Yield ``(item_id, tags)`` pairs for every stored item."""

        for item_id, tags in self._records.items():
            yield item_id, list(tags)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _normalize_item_id(self, item_id: str) -> str:
        value = str(item_id)
        if not value:
            raise ValueError("Item identifier cannot be empty.")
        return value

    def _normalize_tag(self, tag: str) -> str:
        value = str(tag).strip()
        if not value:
            raise ValueError("Tags must contain visible characters.")
        return value

    def _normalize_tag_iterable(self, tags: Iterable[str]) -> list[str]:
        unique: dict[str, None] = {}
        for tag in tags:
            normalized = self._normalize_tag(tag)
            unique.setdefault(normalized, None)
        return sorted(unique.keys(), key=str.casefold)

    def _load(self) -> None:
        if not self._path.exists():
            self._records = {}
            return

        try:
            with self._path.open("r", encoding="utf8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            self._records = {}
            return

        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, dict):
            self._records = {}
            return

        records: dict[str, list[str]] = {}

        for item_id, tags in items.items():
            if not isinstance(tags, list):
                continue

            key = str(item_id)
            normalized_tags = [
                str(tag).strip() for tag in tags if str(tag).strip()
            ]

            if normalized_tags:
                records[key] = sorted(set(normalized_tags), key=str.casefold)

        self._records = records

    def _save(self) -> None:
        if not self._records:
            # Remove the file entirely when no tags remain to avoid leaving
            # behind empty JSON files.
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"items": self._records}

        with self._path.open("w", encoding="utf8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
