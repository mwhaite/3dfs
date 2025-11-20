"""High level service facade for interacting with the asset repository."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from ..gcode import (
    DEFAULT_GCODE_PREVIEW_SIZE,
    GCodeAnalysis,
    GCodePreviewCache,
    GCodePreviewError,
    GCodePreviewResult,
    analyze_gcode_program,
    extract_render_hints,
)
from ..importer import GCODE_EXTENSIONS
from ..thumbnails import (
    DEFAULT_THUMBNAIL_SIZE,
    ThumbnailCache,
    ThumbnailGenerationError,
    ThumbnailManager,
    ThumbnailResult,
)
from .repository import (
    AssetRecord,
    AssetRelationshipRecord,
    AssetRepository,
    ContainerVersionRecord,
    CustomizationRecord,
)

__all__ = [
    "AssetSeed",
    "AssetService",
    "TagGraph",
    "TagGraphLink",
    "TagGraphNode",
]


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
        label="Container overview",
        metadata={"description": "High level summary of the 3dfs container."},
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
        gcode_preview_cache: GCodePreviewCache | None = None,
    ) -> None:
        self._repository = repository or AssetRepository()
        self._thumbnail_cache = thumbnail_cache
        self._thumbnail_manager: ThumbnailManager | None = None
        self._gcode_preview_cache = gcode_preview_cache

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

    def get_asset(self, asset_id: int) -> AssetRecord | None:
        """Fetch an asset by numeric identifier."""

        return self._repository.get_asset(asset_id)

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

    # ------------------------------------------------------------------
    # Tag graph operations
    # ------------------------------------------------------------------
    def build_tag_graph(
        self,
        *,
        min_cooccurrence: int = 1,
        max_tags: int | None = 50,
    ) -> TagGraph:
        """Return a lightweight graph describing tag co-occurrence.

        Parameters
        ----------
        min_cooccurrence:
            Minimum number of shared assets required before an edge between two
            tags is emitted.
        max_tags:
            Optional cap for the number of tag nodes. When provided the graph is
            limited to the most common tags by asset count.
        """

        tag_counts: Counter[str] = Counter()
        edge_counts: Counter[tuple[str, str]] = Counter()

        for _path, tags in self._repository.iter_tagged_assets():
            unique_tags = sorted({str(tag).strip() for tag in tags if tag})
            if not unique_tags:
                continue
            for tag in unique_tags:
                tag_counts[tag] += 1
            for first, second in combinations(unique_tags, 2):
                edge_counts[(first, second)] += 1

        if not tag_counts:
            return TagGraph((), ())

        if max_tags is not None and max_tags > 0:
            top_tags = {tag for tag, _count in tag_counts.most_common(max_tags)}
        else:
            top_tags = set(tag_counts)

        nodes = [TagGraphNode(name=tag, count=count) for tag, count in tag_counts.items() if tag in top_tags]
        nodes.sort(key=lambda node: node.count, reverse=True)

        edges: list[TagGraphLink] = []
        for (first, second), weight in edge_counts.items():
            if weight < max(1, min_cooccurrence):
                continue
            if first not in top_tags or second not in top_tags:
                continue
            edges.append(TagGraphLink(source=first, target=second, weight=weight))

        edges.sort(key=lambda link: link.weight, reverse=True)

        return TagGraph(nodes=tuple(nodes), links=tuple(edges))

    def delete_asset_by_path(self, path: str) -> bool:
        """Remove an asset identified by *path*."""

        return self._repository.delete_asset_by_path(path)

    def prune_missing_assets(self, *, base_path: Path | None = None) -> int:
        """Remove asset records whose backing files or folders are gone.

        If *base_path* is provided, relative asset paths are resolved against it.
        Paths that cannot be resolved safely are skipped.
        """

        root = None
        if base_path is not None:
            try:
                root = base_path.expanduser().resolve()
            except Exception:  # noqa: BLE001 - keep pruning best-effort
                root = None

        removed = 0
        for asset in self._repository.list_assets():
            raw_path = asset.path
            resolved: Path | None
            try:
                candidate = Path(raw_path).expanduser()
            except Exception:  # noqa: BLE001 - malformed path strings
                candidate = None

            if candidate is None:
                resolved = None
            elif candidate.is_absolute():
                resolved = candidate
            else:
                if root is None:
                    # Without a root we cannot safely resolve a relative path.
                    continue
                try:
                    resolved = (root / candidate).resolve()
                except Exception:  # noqa: BLE001 - resolution can fail on bad data
                    resolved = None
                else:
                    try:
                        resolved.relative_to(root)
                    except ValueError:
                        # Escapes the library root; ignore rather than pruning.
                        continue

            should_prune = False
            if resolved is None:
                should_prune = True
            else:
                try:
                    should_prune = not resolved.exists()
                except OSError:
                    should_prune = True

            if should_prune and self._repository.delete_asset(asset.id):
                removed += 1
                logger.debug("Pruned missing asset: %s", raw_path)

        return removed

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
    # Container version operations
    # ------------------------------------------------------------------
    def create_container_version(
        self,
        container_asset_id: int,
        *,
        name: str,
        metadata: Mapping[str, Any] | None = None,
        notes: str | None = None,
        source_version_id: int | None = None,
    ) -> ContainerVersionRecord:
        """Snapshot the current state of a container asset."""

        snapshot = metadata
        if snapshot is None:
            asset = self.get_asset(container_asset_id)
            if asset is None:
                raise ValueError("Cannot version unknown container asset")
            snapshot = asset.metadata
        return self._repository.create_container_version(
            container_asset_id,
            name=name,
            metadata=snapshot,
            notes=notes,
            source_version_id=source_version_id,
        )

    def list_container_versions(self, container_asset_id: int) -> list[ContainerVersionRecord]:
        """Return stored versions for *container_asset_id*."""

        return self._repository.list_container_versions(container_asset_id)

    def get_container_versions(self, container_asset_id: int) -> list[ContainerVersionRecord]:
        """Alias for :meth:`list_container_versions` for API clarity."""

        return self.list_container_versions(container_asset_id)

    def get_container_version(self, version_id: int) -> ContainerVersionRecord | None:
        """Return a specific container version by identifier."""

        return self._repository.get_container_version(version_id)

    def get_latest_container_version(self, container_asset_id: int) -> ContainerVersionRecord | None:
        """Return the most recently created version for a container."""

        return self._repository.get_latest_container_version(container_asset_id)

    def delete_container_version(self, version_id: int) -> bool:
        """Remove a stored container version."""

        return self._repository.delete_container_version(version_id)

    def rename_container_version(self, version_id: int, *, name: str) -> ContainerVersionRecord:
        """Rename an existing container version."""

        return self._repository.rename_container_version(version_id, name=name)

    # ------------------------------------------------------------------
    # Customization operations
    # ------------------------------------------------------------------
    def create_customization(
        self,
        base_path: str,
        *,
        backend_identifier: str,
        parameter_schema: Mapping[str, Any] | None = None,
        parameter_values: Mapping[str, Any] | None = None,
    ) -> CustomizationRecord:
        """Create a customization for the asset located at *base_path*."""

        base_asset = self.ensure_asset(base_path, label=base_path)
        return self._repository.create_customization(
            base_asset.id,
            backend_identifier=backend_identifier,
            parameter_schema=parameter_schema,
            parameter_values=parameter_values,
        )

    def get_customization(self, customization_id: int) -> CustomizationRecord | None:
        """Return the customization identified by *customization_id*."""

        return self._repository.get_customization(customization_id)

    def list_customizations_for_asset(self, base_path: str) -> list[CustomizationRecord]:
        """Return all customizations associated with *base_path*."""

        asset = self.get_asset_by_path(base_path)
        if asset is None:
            return []
        return self._repository.list_customizations_for_asset(asset.id)

    def update_customization(
        self,
        customization_id: int,
        *,
        backend_identifier: str | None = None,
        parameter_schema: Mapping[str, Any] | None = None,
        parameter_values: Mapping[str, Any] | None = None,
    ) -> CustomizationRecord:
        """Update an existing customization record."""

        kwargs: dict[str, Any] = {}
        if backend_identifier is not None:
            kwargs["backend_identifier"] = backend_identifier
        if parameter_schema is not None:
            kwargs["parameter_schema"] = parameter_schema
        if parameter_values is not None:
            kwargs["parameter_values"] = parameter_values
        return self._repository.update_customization(customization_id, **kwargs)

    def delete_customization(self, customization_id: int) -> bool:
        """Remove the customization identified by *customization_id*."""

        return self._repository.delete_customization(customization_id)

    def record_derivative(
        self,
        customization_id: int,
        derivative_path: str,
        *,
        relationship_type: str,
        label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        tags: Iterable[str] | None = None,
    ) -> tuple[AssetRecord, AssetRelationshipRecord]:
        """Ensure a derivative asset exists and link it to a customization."""

        derivative_label = label or derivative_path
        derivative_asset = self.ensure_asset(
            derivative_path,
            label=derivative_label,
            metadata=metadata,
        )

        update_kwargs: dict[str, Any] = {}
        if label is not None:
            update_kwargs["label"] = label
        if metadata is not None:
            update_kwargs["metadata"] = metadata
        if tags is not None:
            update_kwargs["tags"] = list(tags)
        if update_kwargs:
            derivative_asset = self._repository.update_asset(
                derivative_asset.id,
                **update_kwargs,
            )

        relationship = self._repository.create_asset_relationship(
            customization_id,
            derivative_asset.id,
            relationship_type,
        )
        return derivative_asset, relationship

    def list_derivatives_for_asset(
        self,
        base_path: str,
        *,
        relationship_type: str | None = None,
    ) -> list[AssetRecord]:
        """Return derivative assets generated from *base_path*."""

        asset = self.get_asset_by_path(base_path)
        if asset is None:
            return []
        return self._repository.list_derivatives_for_asset(asset.id, relationship_type=relationship_type)

    def get_base_for_derivative(
        self,
        derivative_path: str,
        *,
        relationship_type: str | None = None,
    ) -> AssetRecord | None:
        """Return the originating asset for *derivative_path* if known."""

        derivative = self.get_asset_by_path(derivative_path)
        if derivative is None:
            return None
        return self._repository.get_base_for_derivative(derivative.id, relationship_type=relationship_type)

    # ------------------------------------------------------------------
    # Tag operations
    # ------------------------------------------------------------------
    def tags_for_path(self, path: str) -> list[str]:
        """Return the tag list for *path*."""

        asset = self.get_asset_by_path(path)
        if asset is None:
            return []
        return self._repository.tags_for_asset_id(asset.id)

    def tags_for_asset(self, asset_id: int) -> list[str]:
        """Return the tag list for the asset identified by *asset_id*."""

        asset = self.get_asset(asset_id)
        if asset is None:
            return []
        return self._repository.tags_for_asset_id(asset.id)

    def set_tags(self, path: str, tags: Iterable[str]) -> list[str]:
        """Replace the tag list for *path*."""

        asset = self.get_asset_by_path(path)
        if asset is None:
            raise ValueError("Asset does not exist: cannot set tags")
        return self._repository.set_tags(asset.id, tags)

    def set_tags_for_asset(self, asset_id: int, tags: Iterable[str]) -> list[str]:
        asset = self.get_asset(asset_id)
        if asset is None:
            raise ValueError("Asset does not exist: cannot set tags")
        return self._repository.set_tags(asset.id, tags)

    def add_tag(self, path: str, tag: str) -> str | None:
        """Assign *tag* to the asset identified by *path*."""

        asset = self.get_asset_by_path(path)
        if asset is None:
            raise ValueError("Asset does not exist: cannot add tag")
        return self._repository.add_tag(asset.id, tag)

    def add_tag_to_asset(self, asset_id: int, tag: str) -> str | None:
        asset = self.get_asset(asset_id)
        if asset is None:
            raise ValueError("Asset does not exist: cannot add tag")
        return self._repository.add_tag(asset.id, tag)

    def remove_tag(self, path: str, tag: str) -> bool:
        """Remove *tag* from the asset identified by *path*."""

        asset = self.get_asset_by_path(path)
        if asset is None:
            return False
        return self._repository.remove_tag(asset.id, tag)

    def remove_tag_from_asset(self, asset_id: int, tag: str) -> bool:
        asset = self.get_asset(asset_id)
        if asset is None:
            raise ValueError("Asset does not exist: cannot remove tag")
        return self._repository.remove_tag(asset.id, tag)

    def rename_tag(self, path: str, old_tag: str, new_tag: str) -> str | None:
        """Rename *old_tag* to *new_tag* for the asset identified by *path*."""

        asset = self.get_asset_by_path(path)
        if asset is None:
            return None
        return self._repository.rename_tag(asset.id, old_tag, new_tag)

    def rename_tag_for_asset(self, asset_id: int, old_tag: str, new_tag: str) -> str | None:
        asset = self.get_asset(asset_id)
        if asset is None:
            raise ValueError("Asset does not exist: cannot rename tag")
        return self._repository.rename_tag(asset.id, old_tag, new_tag)

    def search_tags(self, query: str) -> dict[str, list[str]]:
        """Return a mapping of paths to tags matching *query*."""

        results = self._repository.search_tags(query)
        filtered: dict[str, list[str]] = {}
        for path, tags in results.items():
            if self.get_asset_by_path(path) is not None:
                filtered[path] = tags
        return filtered

    def all_tags(self) -> list[str]:
        """Return the universe of known tag names."""

        return self._repository.all_tags()

    def iter_tagged_assets(self) -> Iterator[tuple[str, list[str]]]:
        """Yield ``(path, tags)`` pairs for assets that have tags."""

        yield from self._repository.iter_tagged_assets()

    def paths_for_tag(self, tag: str) -> list[str]:
        normalized = str(tag).strip()
        if not normalized:
            return []
        paths = self._repository.paths_for_tag(normalized)
        filtered: list[str] = []
        for path in paths:
            if self.get_asset_by_path(path) is not None:
                filtered.append(path)
        return filtered

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

        metadata = dict(asset.metadata or {})
        thumbnail_meta = metadata.get("thumbnail")

        def _resolve_container_root() -> Path | None:
            container_root = metadata.get("container_path")
            if not isinstance(container_root, str) or not container_root:
                return None
            try:
                return Path(container_root).expanduser().resolve()
            except Exception:
                return None

        if isinstance(thumbnail_meta, Mapping) and thumbnail_meta.get("source") == "viewer_capture":
            container_root_path = _resolve_container_root()
            path_hint = thumbnail_meta.get("absolute_path") or thumbnail_meta.get("path")
            candidate_path: Path | None = None
            if isinstance(path_hint, str) and path_hint.strip():
                candidate_path = Path(path_hint)
                if not candidate_path.is_absolute():
                    if container_root_path is not None:
                        candidate_path = container_root_path / candidate_path
                    else:
                        try:
                            candidate_path = Path(asset.path).expanduser().resolve().parent / candidate_path
                        except Exception:
                            candidate_path = None
            if candidate_path is not None:
                resolved = candidate_path.expanduser()
                if resolved.exists():
                    try:
                        payload = resolved.read_bytes()
                    except OSError:
                        payload = None
                    else:
                        info = dict(thumbnail_meta)
                        info.setdefault("path", resolved.as_posix())
                        info.setdefault("absolute_path", resolved.as_posix())
                        if "relative_path" not in info and container_root_path is not None:
                            try:
                                rel = resolved.resolve().relative_to(container_root_path)
                            except Exception:
                                rel = None
                            if rel is not None:
                                info["relative_path"] = rel.as_posix()
                        result = ThumbnailResult(
                            path=resolved,
                            info=info,
                            image_bytes=payload,
                            updated=False,
                        )
                        return asset, result

        manager = self._ensure_thumbnail_manager()

        try:
            result = manager.render_for_asset(asset, size=size)
        except ThumbnailGenerationError as exc:
            logger.debug("Unable to generate thumbnail for %s: %s", asset.path, exc)
            return asset, None
        except Exception:  # pragma: no cover - defensive safeguard
            logger.exception("Unexpected error while generating thumbnail for %s", asset.path)
            return asset, None

        metadata = dict(asset.metadata)
        existing = metadata.get("thumbnail")
        if existing != result.info:
            metadata["thumbnail"] = result.info
            updated = self.update_asset(asset.id, metadata=metadata)
            return updated, result

        return asset, result

    def ensure_gcode_preview(
        self,
        asset: AssetRecord,
        *,
        size: tuple[int, int] = DEFAULT_GCODE_PREVIEW_SIZE,
        hints: Mapping[str, str] | None = None,
        analysis: GCodeAnalysis | None = None,
    ) -> tuple[AssetRecord, GCodePreviewResult | None]:
        """Ensure *asset* has a cached G-code preview returning the result."""

        try:
            source_path = Path(asset.path).expanduser()
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.debug("Invalid path for asset %s: %s", asset.path, exc)
            return asset, None

        metadata = dict(asset.metadata or {})
        existing = metadata.get("gcode_preview") if isinstance(metadata, Mapping) else None
        existing_info = existing if isinstance(existing, Mapping) else None

        hint_map = dict(hints or {})
        if not hint_map:
            try:
                tag_values = self.tags_for_path(asset.path)
            except Exception:
                tag_values = []
            hint_map = extract_render_hints(tag_values)

        cache = self._ensure_gcode_preview_cache()

        try:
            program = analysis or analyze_gcode_program(source_path)
        except GCodePreviewError as exc:
            logger.debug("Unable to analyse G-code for %s: %s", asset.path, exc)
            return asset, None
        except Exception:  # pragma: no cover - defensive safeguard
            logger.exception("Unexpected error while analysing G-code for %s", asset.path)
            return asset, None

        try:
            result = cache.get_or_render(
                source_path,
                hints=hint_map,
                existing_info=existing_info,
                size=size,
                analysis=program,
            )
        except GCodePreviewError as exc:
            logger.debug("Unable to generate G-code preview for %s: %s", asset.path, exc)
            return asset, None
        except Exception:  # pragma: no cover - defensive safeguard
            logger.exception("Unexpected error while generating G-code preview for %s", asset.path)
            return asset, None

        if existing_info != result.info:
            metadata["gcode_preview"] = result.info
            updated = self.update_asset(asset.id, metadata=metadata)
            return updated, result

        return asset, result

    def _ensure_thumbnail_manager(self) -> ThumbnailManager:
        if self._thumbnail_manager is None:
            cache = self._thumbnail_cache or ThumbnailCache()
            self._thumbnail_manager = ThumbnailManager(cache)
        return self._thumbnail_manager

    def _ensure_gcode_preview_cache(self) -> GCodePreviewCache:
        if self._gcode_preview_cache is None:
            self._gcode_preview_cache = GCodePreviewCache()
        return self._gcode_preview_cache

    def ensure_all_gcode_previews(
        self,
        *,
        size: tuple[int, int] = DEFAULT_GCODE_PREVIEW_SIZE,
    ) -> int:
        """Ensure cached previews exist for all recognised G-code assets."""

        generated = 0
        for asset in self.list_assets():
            try:
                suffix = Path(asset.path).suffix.lower()
            except Exception:
                continue
            if suffix not in GCODE_EXTENSIONS:
                continue
            _, result = self.ensure_gcode_preview(asset, size=size)
            if result is not None and result.updated:
                generated += 1
        return generated

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


@dataclass(frozen=True, slots=True)
class TagGraphNode:
    name: str
    count: int


@dataclass(frozen=True, slots=True)
class TagGraphLink:
    source: str
    target: str
    weight: int


@dataclass(frozen=True, slots=True)
class TagGraph:
    nodes: tuple[TagGraphNode, ...]
    links: tuple[TagGraphLink, ...]
