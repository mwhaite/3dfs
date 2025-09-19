"""High level service facade for interacting with the asset repository."""

from __future__ import annotations


import logging
from pathlib import Path

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from typing import Any


from .repository import (
    AssetRecord,
    AssetRelationshipRecord,
    AssetRepository,
    CustomizationRecord,
)

from ..thumbnails import (
    DEFAULT_THUMBNAIL_SIZE,
    ThumbnailCache,
    ThumbnailGenerationError,
    ThumbnailManager,
    ThumbnailResult,
)
from .repository import AssetRecord, AssetRepository



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

    def list_customizations_for_asset(
        self, base_path: str
    ) -> list[CustomizationRecord]:
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
        return self._repository.list_derivatives_for_asset(
            asset.id, relationship_type=relationship_type
        )

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
        return self._repository.get_base_for_derivative(
            derivative.id, relationship_type=relationship_type
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
    # Customization session operations
    # ------------------------------------------------------------------
    def record_customization_session(
        self, session: CustomizerSession
    ) -> CustomizerSession:
        """Persist *session* metadata returning the stored state."""

        base_path = str(session.base_asset_path)
        base_label = Path(base_path).name or base_path
        base_asset = self.ensure_asset(base_path, label=base_label)

        prepared_artifacts: list[GeneratedArtifact] = []
        for artifact in session.artifacts:
            artifact_path = str(artifact.path)
            artifact_label = artifact.label or Path(artifact_path).name or artifact_path
            asset = self.ensure_asset(artifact_path, label=artifact_label)
            prepared_artifacts.append(
                replace(
                    artifact,
                    path=asset.path,
                    label=artifact_label,
                    asset_id=asset.id,
                )
            )

        payload = session.to_dict()
        payload["base_asset_path"] = base_asset.path
        payload["artifacts"] = [artifact.to_dict() for artifact in prepared_artifacts]

        record = self._repository.create_customization(base_asset.id, payload)

        for artifact in prepared_artifacts:
            if artifact.asset_id is None:
                continue
            self._repository.attach_generated_asset(
                record.id, artifact.asset_id, artifact.relationship
            )

        return CustomizerSession.from_dict(payload, session_id=record.id)

    def get_customization_session(
        self, session_id: int
    ) -> CustomizerSession | None:
        """Return the persisted session identified by *session_id*."""

        record = self._repository.get_customization(session_id)
        if record is None:
            return None

        base_asset = self._repository.get_asset(record.base_asset_id)
        session = CustomizerSession.from_dict(record.payload, session_id=record.id)
        base_path = base_asset.path if base_asset is not None else session.base_asset_path

        relationships = self._repository.generated_assets_for_customization(record.id)
        relationship_map = {
            relation.generated_asset_id: relation.relationship_type
            for relation in relationships
        }

        resolved_artifacts: list[GeneratedArtifact] = []
        for artifact in session.artifacts:
            asset_record = None
            if artifact.asset_id is not None:
                asset_record = self._repository.get_asset(artifact.asset_id)
            if asset_record is not None:
                relationship = relationship_map.get(
                    asset_record.id, artifact.relationship
                )
                resolved_artifact = replace(
                    artifact,
                    path=asset_record.path,
                    label=artifact.label or asset_record.label,
                    relationship=relationship,
                    asset_id=asset_record.id,
                )
            else:
                resolved_artifact = artifact
            resolved_artifacts.append(resolved_artifact)

        return replace(
            session,
            base_asset_path=base_path,
            artifacts=tuple(resolved_artifacts),
        )

    def list_customization_sessions(
        self, base_asset_path: str
    ) -> list[CustomizerSession]:
        """Return all sessions associated with *base_asset_path*."""

        asset = self.get_asset_by_path(base_asset_path)
        if asset is None:
            return []

        records = self._repository.list_customizations_for_asset(asset.id)
        sessions: list[CustomizerSession] = []
        for record in records:
            session = self.get_customization_session(record.id)
            if session is not None:
                sessions.append(session)
        return sessions

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
