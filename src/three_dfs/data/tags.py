"""Tag persistence helpers backed by the asset storage layer."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from ..storage import AssetRepository, AssetService, SQLiteStorage

__all__ = ["TagStore"]


class TagStore:
    """Persist and query tag assignments for repository items."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        service: AssetService | None = None,
    ) -> None:
        if path is not None and service is not None:
            raise ValueError("Provide either a database path or an AssetService, not both.")

        if service is None:
            storage = SQLiteStorage(path)
            repository = AssetRepository(storage)
            service = AssetService(repository)

        self._service = service
        self._repository = service.repository

    # ------------------------------------------------------------------
    # Basic helpers
    # ------------------------------------------------------------------
    @property
    def path(self) -> Path | None:
        """Return the resolved location of the SQLite database if persisted."""

        return self._repository.database_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def tags_for(self, item_id: str) -> list[str]:
        """Return a copy of the tags assigned to *item_id*."""

        item_key = self._normalize_item_id(item_id)
        return self._service.tags_for_path(item_key)

    def set_tags(self, item_id: str, tags: Iterable[str]) -> list[str]:
        """Replace the tags for *item_id* with *tags*."""

        item_key = self._normalize_item_id(item_id)
        normalized = self._normalize_tag_iterable(tags)
        return self._service.set_tags(item_key, normalized)

    def add_tag(self, item_id: str, tag: str) -> str | None:
        """Add *tag* to *item_id* and return the normalized value."""

        item_key = self._normalize_item_id(item_id)
        normalized = self._normalize_tag(tag)
        return self._service.add_tag(item_key, normalized)

    def remove_tag(self, item_id: str, tag: str) -> bool:
        """Remove *tag* from *item_id* if present."""

        item_key = self._normalize_item_id(item_id)
        normalized = self._normalize_tag(tag)
        return self._service.remove_tag(item_key, normalized)

    def rename_tag(self, item_id: str, old_tag: str, new_tag: str) -> str | None:
        """Rename *old_tag* to *new_tag* for *item_id*."""

        item_key = self._normalize_item_id(item_id)
        old_normalized = self._normalize_tag(old_tag)
        new_normalized = self._normalize_tag(new_tag)
        return self._service.rename_tag(item_key, old_normalized, new_normalized)

    def search(self, query: str) -> dict[str, list[str]]:
        """Return all tags whose text contains *query* (case-insensitive)."""

        return self._service.search_tags(query)

    def all_tags(self) -> list[str]:
        """Return a sorted list of every tag stored for any item."""

        return self._service.all_tags()

    def iter_items(self) -> Iterator[tuple[str, list[str]]]:
        """Yield ``(item_id, tags)`` pairs for every stored item."""

        yield from self._service.iter_tagged_assets()

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
