"Customization execution pipeline that persists generated artifacts."

from __future__ import annotations

import logging
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
from ..paths import resolve_storage_root
from ..storage import (
    AssetRecord,
    AssetRelationshipRecord,
    AssetService,
    CustomizationRecord,
    build_asset_metadata,
)
from ..storage.container_service import ContainerService
from . import CustomizerBackend, GeneratedArtifact

__all__ = ["ArtifactResult", "PipelineResult", "execute_customization"]


logger = logging.getLogger(__name__)


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
    output_path: str
    customization_id: int
    parameters: dict[str, Any]
    generated_at: datetime
    container: AssetRecord | None = None
    container_path: Path | None = None


def execute_customization(
    base_asset: AssetRecord,
    backend: CustomizerBackend,
    parameters: Mapping[str, Any],
    *,
    asset_service: AssetService,
    container_name: str | None = None,
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

    managed_root = resolve_storage_root(
        storage_root,
        default=default_storage_root,
    )
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

        normalized_parameters = dict(session.parameters)

        customization_record = asset_service.create_customization(
            base_asset.path,
            backend_identifier=backend_identifier,
            parameter_schema=session.schema.to_dict(),
            parameter_values=normalized_parameters,
        )

        container_service = ContainerService(asset_service)
        source_container = container_service.find_container_for_asset(base_asset)

        container_metadata = {
            "created_from_customizer": True,
            "customization_id": customization_record.id,
            "customization_backend": backend_identifier,
            "source_asset_id": base_asset.id,
            "source_asset_path": base_asset.path,
            "source_asset_label": base_asset.label,
        }
        if source_container:
            container_metadata["source_container_id"] = source_container.id
        container_metadata["container_type"] = "part"

        container_label = _derive_container_name(base_asset, normalized_parameters, container_name)

        new_container_asset, container_folder = container_service.create_container(
            name=container_label,
            root=managed_root,
            metadata=container_metadata,
        )

        created_asset_ids: set[int] = set()
        primary_output_path: str | None = None
        artifact_results: list[ArtifactResult] = []
        for index, artifact in enumerate(session.artifacts, start=1):
            if artifact.asset_id is not None:
                existing_asset = asset_service.repository.get_asset(artifact.asset_id)
                if existing_asset is None:
                    raise LookupError("Generated artifact references unknown asset " f"{artifact.asset_id}")

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
                if primary_output_path is None and _is_primary_output(artifact):
                    primary_output_path = existing_asset.path
                continue

            source_path = Path(artifact.path)
            if not source_path.exists():
                raise FileNotFoundError(f"Generated artifact {source_path!s} does not exist")

            proposed_name = source_path.name or f"artifact_{index}"
            destination_root = container_folder
            destination = _allocate_destination(destination_root, proposed_name)
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

            created_asset_ids.add(asset_record.id)

            artifact_results.append(
                ArtifactResult(
                    artifact=artifact,
                    asset=asset_record,
                    relationship=relationship,
                )
            )
            if primary_output_path is None and _is_primary_output(artifact):
                primary_output_path = asset_record.path

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

        components = []
        for result in artifact_results:
            components.append(
                {
                    "asset_id": result.asset.id,
                    "path": result.asset.path,
                    "label": result.asset.label,
                }
            )

        if components:
            updated_metadata = dict(new_container_asset.metadata)
            updated_metadata["components"] = components
            new_container_asset = asset_service.update_asset(
                new_container_asset.id,
                metadata=updated_metadata,
            )

        if source_container is not None:
            updated_source, updated_target = container_service.link_containers(
                source_container,
                new_container_asset,
                link_type="customization",
            )
            source_container = updated_source
            new_container_asset = updated_target

        if primary_output_path is None and artifact_results:
            primary_output_path = artifact_results[0].asset.path

        return PipelineResult(
            base_asset=base_asset,
            customization=customization_record,
            artifacts=tuple(artifact_results),
            working_directory=work_directory,
            output_path=primary_output_path or base_asset.path,
            customization_id=customization_record.id,
            parameters=dict(normalized_parameters),
            generated_at=customization_record.created_at,
            container=new_container_asset,
            container_path=container_folder,
        )
    finally:
        if cleanup:
            shutil.rmtree(work_directory, ignore_errors=True)


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

    raise FileNotFoundError(f"No readable source file found for base asset {base_asset.path!s}")


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
    size = destination.stat().st_size
    metadata = build_asset_metadata(
        source=base_asset.path,
        source_type="customization",
        original_path=source_path,
        managed_path=destination,
        size=size,
        timestamps={"generated_at": generated_at},
    )

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

        content_type = result.artifact.content_type or _guess_content_type(result.asset.path)
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


def _is_primary_output(artifact: GeneratedArtifact) -> bool:
    relation = artifact.relationship.casefold()
    if relation in {"output", "model", "mesh"}:
        return True
    suffix = Path(artifact.path).suffix.lower()
    return suffix in {".stl", ".obj", ".ply", ".glb", ".gltf", ".3mf"}


def _safe_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _select_primary_artifacts(
    results: Sequence[ArtifactResult],
) -> tuple[ArtifactResult | None, ArtifactResult | None]:
    model_result: ArtifactResult | None = None
    source_result: ArtifactResult | None = None

    for result in results:
        path = Path(result.asset.path)
        content_type = (result.artifact.content_type or "").lower()
        suffix = path.suffix.lower()
        if model_result is None and (
            content_type.startswith("model/") or suffix in {".stl", ".obj", ".ply", ".glb", ".gltf", ".3mf"}
        ):
            model_result = result
        if source_result is None and suffix == ".scad":
            source_result = result
        if model_result is not None and source_result is not None:
            break

    return model_result, source_result


def _attachment_entry_from_asset(
    asset: AssetRecord,
    *,
    container_folder: Path | None,
    relationship: str,
    content_type: str | None,
) -> dict[str, Any]:
    path = Path(asset.path)
    entry: dict[str, Any] = {
        "path": asset.path,
        "asset_id": asset.id,
        "label": asset.label or path.name,
        "relationship": relationship,
        "suffix": path.suffix,
        "relative_path": _relative_path_within(path, container_folder),
    }
    if content_type:
        entry["content_type"] = content_type
    try:
        stat = path.stat()
    except OSError:
        stat = None
    if stat is not None:
        entry["file_size"] = stat.st_size
    return entry


def _relative_path_within(path: Path, root: Path | None) -> str:
    if root is None:
        return path.name
    try:
        resolved_root = root.expanduser().resolve()
        resolved_path = path.expanduser().resolve()
        rel = resolved_path.relative_to(resolved_root)
        text = rel.as_posix()
        return text if text and text != "." else path.name
    except Exception:
        return path.name


def _derive_container_name(
    base_asset: AssetRecord,
    parameters: Mapping[str, Any],
    explicit_name: str | None,
) -> str:
    if isinstance(explicit_name, str) and explicit_name.strip():
        return explicit_name.strip()

    base_label = base_asset.label or Path(base_asset.path).stem
    if not base_label:
        base_label = f"Asset {base_asset.id}"

    summary = _summarize_parameters(parameters)
    if summary:
        return f"{base_label} ({summary})"
    return base_label


def _summarize_parameters(values: Mapping[str, Any], *, limit: int = 3) -> str:
    if not values:
        return ""
    pieces: list[str] = []
    for index, name in enumerate(sorted(values)):
        formatted = _format_parameter_value(values[name])
        pieces.append(f"{name}={formatted}")
        if index + 1 >= limit:
            break
    return ", ".join(pieces)


def _format_parameter_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = f"{value:.4f}"
        text = text.rstrip("0").rstrip(".")
        return text or "0"
    return str(value)
