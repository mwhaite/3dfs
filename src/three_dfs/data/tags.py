"""Tag persistence helpers backed by the asset storage layer."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from ..storage import AssetRecord, AssetRepository, AssetService, SQLiteStorage

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

    def tags_for_asset(self, asset_id: int) -> list[str]:
        """Return the tags assigned to the asset identified by *asset_id*."""

        return self._service.tags_for_asset(asset_id)

    def set_tags(self, item_id: str, tags: Iterable[str]) -> list[str]:
        """Replace the tags for *item_id* with *tags*."""

        item_key = self._normalize_item_id(item_id)
        normalized = self._normalize_tag_iterable(tags)
        self._ensure_item_asset(item_key)
        return self._service.set_tags(item_key, normalized)

    def set_tags_for_asset(self, asset_id: int, tags: Iterable[str]) -> list[str]:
        normalized = self._normalize_tag_iterable(tags)
        return self._repository.set_tags(asset_id, normalized)

    def add_tag(self, item_id: str, tag: str) -> str | None:
        """Add *tag* to *item_id* and return the normalized value."""

        item_key = self._normalize_item_id(item_id)
        normalized = self._normalize_tag(tag)
        self._ensure_item_asset(item_key)
        return self._service.add_tag(item_key, normalized)

    def add_tag_to_asset(self, asset_id: int, tag: str) -> str | None:
        normalized = self._normalize_tag(tag)
        try:
            return self._service.add_tag_to_asset(asset_id, normalized)
        except ValueError:
            return None

    def remove_tag(self, item_id: str, tag: str) -> bool:
        """Remove *tag* from *item_id* if present."""

        item_key = self._normalize_item_id(item_id)
        normalized = self._normalize_tag(tag)
        return self._service.remove_tag(item_key, normalized)

    def remove_tag_from_asset(self, asset_id: int, tag: str) -> bool:
        normalized = self._normalize_tag(tag)
        try:
            return self._service.remove_tag_from_asset(asset_id, normalized)
        except ValueError:
            return False

    def rename_tag(self, item_id: str, old_tag: str, new_tag: str) -> str | None:
        """Rename *old_tag* to *new_tag* for *item_id*."""

        item_key = self._normalize_item_id(item_id)
        old_normalized = self._normalize_tag(old_tag)
        new_normalized = self._normalize_tag(new_tag)
        return self._service.rename_tag(item_key, old_normalized, new_normalized)

    def rename_tag_for_asset(self, asset_id: int, old_tag: str, new_tag: str) -> str | None:
        old_normalized = self._normalize_tag(old_tag)
        new_normalized = self._normalize_tag(new_tag)
        try:
            return self._service.rename_tag_for_asset(asset_id, old_normalized, new_normalized)
        except ValueError:
            return None

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
        try:
            value = str(item_id)
        except RecursionError as exc:
            raise ValueError("Invalid item identifier") from exc
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

    def _ensure_item_asset(self, item_key: str) -> AssetRecord:
        asset = self._service.get_asset_by_path(item_key)
        if asset is not None:
            return asset
        label = Path(item_key).name or item_key
        return self._service.ensure_asset(item_key, label=label, metadata={})
