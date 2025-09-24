"""Customization execution pipeline that persists generated artifacts."""

from __future__ import annotations

import mimetypes
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..importer import (
    _allocate_destination,
    _resolve_plugin_destination,
    default_storage_root,
)
from ..storage import (
    AssetRecord,
    AssetRelationshipRecord,
    AssetService,
    CustomizationRecord,
)
from . import CustomizerBackend, GeneratedArtifact

__all__ = ["ArtifactResult", "PipelineResult", "execute_customization"]


@dataclass(slots=True)
class ArtifactResult:
    """Describe the persisted asset produced from a single artifact."""

    artifact: GeneratedArtifact
    asset: AssetRecord
    relationship: AssetRelationshipRecord


@dataclass(slots=True)
class PipelineResult:
    """Aggregate the outcomes produced by :func:`execute_customization`."""

    base_asset: AssetRecord
    customization: CustomizationRecord
    artifacts: tuple[ArtifactResult, ...]
    working_directory: Path


def execute_customization(
    base_asset: AssetRecord,
    backend: CustomizerBackend,
    parameters: Mapping[str, Any],
    *,
    asset_service: AssetService,
    storage_root: str | Path | None = None,
    cleanup: bool = True,
) -> PipelineResult:
    """Execute *backend* using *parameters* producing managed artifacts.

    Parameters
    ----------
    base_asset:
        Asset that serves as the source for the customization workflow.
    backend:
        Customizer backend used to plan and execute the workflow.
    parameters:
        Mapping of parameter overrides supplied by the caller.
    asset_service:
        Service used to persist generated assets and relationships.
    storage_root:
        Optional override for the managed storage root. When omitted the
        pipeline stores artifacts under the default importer location.
    cleanup:
        When ``True`` the temporary working directory is removed once the
        workflow completes. Set to ``False`` to retain intermediate files for
        debugging purposes.
    """

    managed_root = _resolve_storage_root(storage_root)
    base_path = _resolve_source_path(base_asset)
    backend_identifier = _backend_identifier(backend)

    work_parent = managed_root / ".customizer_work"
    work_parent.mkdir(parents=True, exist_ok=True)
    work_directory = _allocate_destination(
        work_parent,
        _working_directory_name(base_asset, backend_identifier),
    )
    work_directory.mkdir(parents=True, exist_ok=False)

    try:
        schema = backend.load_schema(base_path)
        session = backend.plan_build(
            base_path,
            schema,
            parameters,
            output_dir=work_directory,
            execute=True,
        )

        customization_record = asset_service.create_customization(
            base_asset.path,
            backend_identifier=backend_identifier,
            parameter_schema=session.schema.to_dict(),
            parameter_values=dict(session.parameters),
        )

        customization_root = (
            managed_root
            / "customizations"
            / str(base_asset.id)
            / str(customization_record.id)
        )
        customization_root.mkdir(parents=True, exist_ok=True)

        artifact_results: list[ArtifactResult] = []
        for index, artifact in enumerate(session.artifacts, start=1):
            if artifact.asset_id is not None:
                existing_asset = asset_service.repository.get_asset(artifact.asset_id)
                if existing_asset is None:
                    raise LookupError(
                        "Generated artifact references unknown asset "
                        f"{artifact.asset_id}"
                    )

                relationship = asset_service.repository.create_asset_relationship(
                    customization_record.id,
                    existing_asset.id,
                    artifact.relationship,
                )

                artifact_results.append(
                    ArtifactResult(
                        artifact=artifact,
                        asset=existing_asset,
                        relationship=relationship,
                    )
                )
                continue

            source_path = Path(artifact.path)
            if not source_path.exists():
                raise FileNotFoundError(
                    f"Generated artifact {source_path!s} does not exist"
                )

            proposed_name = source_path.name or f"artifact_{index}"
            destination = _allocate_destination(customization_root, proposed_name)
            final_destination = _resolve_plugin_destination(destination, {})
            final_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, final_destination)

            metadata = _build_asset_metadata(
                base_asset,
                customization_record,
                backend_identifier,
                artifact,
                source_path,
                final_destination,
                session.command,
                session.metadata,
            )

            label = artifact.label or final_destination.name
            try:
                asset_record = asset_service.create_asset(
                    str(final_destination),
                    label=label,
                    metadata=metadata,
                )
            except Exception:
                final_destination.unlink(missing_ok=True)
                raise

            asset_record, relationship = asset_service.record_derivative(
                customization_record.id,
                asset_record.path,
                relationship_type=artifact.relationship,
                label=label,
                metadata=metadata,
            )

            artifact_results.append(
                ArtifactResult(
                    artifact=artifact,
                    asset=asset_record,
                    relationship=relationship,
                )
            )

        preview_entries = _build_preview_entries(artifact_results)
        if preview_entries:
            refreshed: list[ArtifactResult] = []
            for result in artifact_results:
                metadata = dict(result.asset.metadata)
                customization_meta = metadata.get("customization")
                if not isinstance(customization_meta, Mapping):
                    refreshed.append(result)
                    continue
                if customization_meta.get("id") != customization_record.id:
                    refreshed.append(result)
                    continue

                customization_meta = dict(customization_meta)
                customization_meta["previews"] = preview_entries
                metadata["customization"] = customization_meta
                updated_asset = asset_service.update_asset(
                    result.asset.id,
                    metadata=metadata,
                )
                refreshed.append(
                    ArtifactResult(
                        artifact=result.artifact,
                        asset=updated_asset,
                        relationship=result.relationship,
                    )
                )
            artifact_results = refreshed

        return PipelineResult(
            base_asset=base_asset,
            customization=customization_record,
            artifacts=tuple(artifact_results),
            working_directory=work_directory,
        )
    finally:
        if cleanup:
            shutil.rmtree(work_directory, ignore_errors=True)


def _resolve_storage_root(storage_root: str | Path | None) -> Path:
    if storage_root is None:
        return default_storage_root()

    candidate = Path(storage_root).expanduser()
    if not candidate.is_absolute():
        candidate = candidate.resolve()
    return candidate


def _resolve_source_path(base_asset: AssetRecord) -> Path:
    candidates: list[Path] = []
    metadata = getattr(base_asset, "metadata", {}) or {}
    if isinstance(metadata, Mapping):
        for key in ("managed_path", "original_path"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(Path(value))

    candidates.append(Path(base_asset.path))

    for candidate in candidates:
        resolved = candidate.expanduser()
        if resolved.exists():
            return resolved

    raise FileNotFoundError(
        f"No readable source file found for base asset {base_asset.path!s}"
    )


def _backend_identifier(backend: CustomizerBackend) -> str:
    identifier = getattr(backend, "name", "") or backend.__class__.__name__
    return str(identifier)


def _working_directory_name(base_asset: AssetRecord, backend_identifier: str) -> str:
    seed = Path(base_asset.path).stem or f"asset_{base_asset.id}"
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    safe_backend = backend_identifier.replace("/", "-").replace("\\", "-")
    return f"{seed}_{safe_backend}_{timestamp}"


def _build_asset_metadata(
    base_asset: AssetRecord,
    customization: CustomizationRecord,
    backend_identifier: str,
    artifact: GeneratedArtifact,
    source_path: Path,
    destination: Path,
    command: Sequence[str],
    session_metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).isoformat()
    try:
        source_modified_at = datetime.fromtimestamp(
            Path(base_asset.path).stat().st_mtime,
            tz=UTC,
        ).isoformat()
    except OSError:
        source_modified_at = None
    metadata: dict[str, Any] = {
        "source": base_asset.path,
        "source_type": "customization",
        "original_path": str(source_path),
        "managed_path": str(destination),
        "size": destination.stat().st_size,
        "generated_at": generated_at,
    }

    if artifact.content_type:
        metadata["content_type"] = artifact.content_type

    customization_meta: dict[str, Any] = {
        "id": customization.id,
        "backend": backend_identifier,
        "base_asset_id": base_asset.id,
        "base_asset_path": base_asset.path,
        "base_asset_label": base_asset.label,
        "base_asset_updated_at": base_asset.updated_at.isoformat(),
        "relationship": artifact.relationship,
        "parameters": dict(customization.parameter_values),
        "command": list(command),
        "session_metadata": dict(session_metadata or {}),
        "generated_at": generated_at,
        "label": artifact.label,
    }

    if _is_preview_artifact(artifact, destination):
        customization_meta["is_preview"] = True

    if source_modified_at is not None:
        customization_meta["source_modified_at"] = source_modified_at

    metadata["customization"] = customization_meta
    return metadata


def _is_preview_artifact(artifact: GeneratedArtifact, destination: Path) -> bool:
    relationship = artifact.relationship.casefold()
    if relationship in {"preview", "thumbnail", "render"}:
        return True

    content_type = (artifact.content_type or "").lower()
    if content_type.startswith("image/"):
        return True

    return destination.suffix.lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
    }


def _build_preview_entries(results: Sequence[ArtifactResult]) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    for result in results:
        if not _is_preview_artifact(result.artifact, Path(result.asset.path)):
            continue

        entry: dict[str, Any] = {
            "asset_id": result.asset.id,
            "path": result.asset.path,
            "relationship": result.artifact.relationship,
            "label": result.asset.label,
        }

        content_type = result.artifact.content_type or _guess_content_type(
            result.asset.path
        )
        if content_type:
            entry["content_type"] = content_type

        managed_path = result.asset.metadata.get("managed_path")
        if isinstance(managed_path, str):
            entry["managed_path"] = managed_path

        previews.append(entry)

    return previews


def _guess_content_type(path: str) -> str | None:
    mime_type, _ = mimetypes.guess_type(path)
    return mime_type
