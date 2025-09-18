"""High level service facade for interacting with the asset repository."""

from __future__ import annotations


import logging

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from ..thumbnails import (
    DEFAULT_THUMBNAIL_SIZE,
    ThumbnailCache,
    ThumbnailGenerationError,
    ThumbnailManager,
    ThumbnailResult,
)



__all__ = ["AssetSeed", "AssetService"]


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AssetSeed:
    """Describe an asset used when bootstrapping demo data."""

    path: str
    label: str
    metadata: Mapping[str, Any] | None = None
    tags: tuple[str, ...] = ()


DEFAULT_ASSET_SEEDS: tuple[AssetSeed, ...] = (
    AssetSeed(
        path="docs/overview.md",
        label="Project overview",
        metadata={"description": "High level summary of the 3dfs project."},
        tags=("docs", "overview"),
    ),
    AssetSeed(
        path="assets/textures/terrain.png",
        label="Terrain texture",
        metadata={"description": "Ground texture used by the default scene."},
        tags=("texture", "environment"),
    ),
    AssetSeed(
        path="assets/models/ship.fbx",
        label="Spaceship model",
        metadata={"description": "Primary spacecraft mesh exported from Blender."},
        tags=("model", "vehicle"),
    ),
    AssetSeed(
        path="scripts/build.py",
        label="Build automation script",
        metadata={"description": "Helper script for packaging distributables."},
        tags=("automation", "python"),
    ),
    AssetSeed(
        path="notes/ideas.txt",
        label="Concept notes",
        metadata={"description": "Scratch pad for brainstorming new features."},
        tags=("notes", "ideas"),
    ),
)


class AssetService:
    """Coordinate high level operations on :class:`AssetRecord` objects."""

    def __init__(
        self,
        repository: AssetRepository | None = None,
        *,
        thumbnail_cache: ThumbnailCache | None = None,
    ) -> None:
        self._repository = repository or AssetRepository()
        self._thumbnail_cache = thumbnail_cache
        self._thumbnail_manager: ThumbnailManager | None = None

    # ------------------------------------------------------------------
    # Basic accessors
    # ------------------------------------------------------------------
    @property
    def repository(self) -> AssetRepository:
        """Expose the underlying repository instance."""

        return self._repository

    def list_assets(self) -> list[AssetRecord]:
        """Return all persisted assets."""

        return self._repository.list_assets()

    def get_asset_by_path(self, path: str) -> AssetRecord | None:
        """Fetch an asset by its unique path."""

        return self._repository.get_asset_by_path(path)

    def create_asset(
        self,
        path: str,
        *,
        label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        tags: Iterable[str] | None = None,
    ) -> AssetRecord:
        """Persist a new asset record."""

        return self._repository.create_asset(
            path,
            label=label,
            metadata=metadata,
            tags=tags,
        )

    def ensure_asset(
        self,
        path: str,
        *,
        label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AssetRecord:
        """Return the stored asset for *path* creating one if necessary."""

        return self._repository.ensure_asset(path, label=label, metadata=metadata)

    def delete_asset(self, asset_id: int) -> bool:
        """Remove an asset by its identifier."""

        return self._repository.delete_asset(asset_id)

    def delete_asset_by_path(self, path: str) -> bool:
        """Remove an asset identified by *path*."""

        return self._repository.delete_asset_by_path(path)

    def update_asset(
        self,
        asset_id: int,
        *,
        path: str | None = None,
        label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AssetRecord:
        """Update attributes on an existing asset."""

        kwargs: dict[str, Any] = {}
        if metadata is not None:
            kwargs["metadata"] = metadata
        return self._repository.update_asset(
            asset_id,
            path=path,
            label=label,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Tag operations
    # ------------------------------------------------------------------
    def tags_for_path(self, path: str) -> list[str]:
        """Return the tag list for *path*."""

        return self._repository.tags_for_path(path)

    def set_tags(self, path: str, tags: Iterable[str]) -> list[str]:
        """Replace the tag list for *path*."""

        asset = self.ensure_asset(path, label=path)
        return self._repository.set_tags(asset.id, tags)

    def add_tag(self, path: str, tag: str) -> str | None:
        """Assign *tag* to the asset identified by *path*."""

        asset = self.ensure_asset(path, label=path)
        return self._repository.add_tag(asset.id, tag)

    def remove_tag(self, path: str, tag: str) -> bool:
        """Remove *tag* from the asset identified by *path*."""

        asset = self.get_asset_by_path(path)
        if asset is None:
            return False
        return self._repository.remove_tag(asset.id, tag)

    def rename_tag(self, path: str, old_tag: str, new_tag: str) -> str | None:
        """Rename *old_tag* to *new_tag* for the asset identified by *path*."""

        asset = self.get_asset_by_path(path)
        if asset is None:
            return None
        return self._repository.rename_tag(asset.id, old_tag, new_tag)

    def search_tags(self, query: str) -> dict[str, list[str]]:
        """Return a mapping of paths to tags matching *query*."""

        return self._repository.search_tags(query)

    def all_tags(self) -> list[str]:
        """Return the universe of known tag names."""

        return self._repository.all_tags()

    def iter_tagged_assets(self) -> Iterator[tuple[str, list[str]]]:
        """Yield ``(path, tags)`` pairs for assets that have tags."""

        return self._repository.iter_tagged_assets()

    # ------------------------------------------------------------------
    # Thumbnail operations
    # ------------------------------------------------------------------
    def ensure_thumbnail(
        self,
        asset: AssetRecord,
        *,
        size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
    ) -> tuple[AssetRecord, ThumbnailResult | None]:
        """Ensure *asset* has a cached thumbnail returning the result."""

        manager = self._ensure_thumbnail_manager()

        try:
            result = manager.render_for_asset(asset, size=size)
        except ThumbnailGenerationError as exc:
            logger.debug("Unable to generate thumbnail for %s: %s", asset.path, exc)
            return asset, None
        except Exception:  # pragma: no cover - defensive safeguard
            logger.exception(
                "Unexpected error while generating thumbnail for %s", asset.path
            )
            return asset, None

        metadata = dict(asset.metadata)
        existing = metadata.get("thumbnail")
        if existing != result.info:
            metadata["thumbnail"] = result.info
            updated = self.update_asset(asset.id, metadata=metadata)
            return updated, result

        return asset, result

    def _ensure_thumbnail_manager(self) -> ThumbnailManager:
        if self._thumbnail_manager is None:
            cache = self._thumbnail_cache or ThumbnailCache()
            self._thumbnail_manager = ThumbnailManager(cache)
        return self._thumbnail_manager

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------
    def bootstrap_demo_data(self) -> list[AssetRecord]:
        """Populate the repository with default entries when empty."""

        existing = self.list_assets()
        if existing:
            return existing

        for seed in DEFAULT_ASSET_SEEDS:
            metadata = dict(seed.metadata) if seed.metadata is not None else None
            self.create_asset(
                seed.path,
                label=seed.label,
                metadata=metadata,
                tags=seed.tags,
            )

        return self.list_assets()
