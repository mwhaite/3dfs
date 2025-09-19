"""Customization backends and session data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence

__all__ = [
    "ParameterDescriptor",
    "ParameterSchema",
    "GeneratedArtifact",
    "CustomizerSession",
    "CustomizerBackend",
]


@dataclass(slots=True)
class ParameterDescriptor:
    """Describe a single configurable value exposed by a backend."""

    name: str
    kind: str
    default: Any
    description: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[Any, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of the descriptor."""

        return {
            "name": self.name,
            "kind": self.kind,
            "default": self.default,
            "description": self.description,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
            "choices": list(self.choices),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParameterDescriptor":
        """Hydrate a descriptor from :meth:`to_dict` payloads."""

        return cls(
            name=str(data["name"]),
            kind=str(data["kind"]),
            default=data.get("default"),
            description=data.get("description"),
            minimum=data.get("minimum"),
            maximum=data.get("maximum"),
            step=data.get("step"),
            choices=tuple(data.get("choices") or ()),
        )


@dataclass(slots=True)
class ParameterSchema:
    """Collection of :class:`ParameterDescriptor` objects for a backend."""

    parameters: tuple[ParameterDescriptor, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the schema for persistence."""

        return {
            "parameters": [descriptor.to_dict() for descriptor in self.parameters],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParameterSchema":
        """Reconstruct a schema from :meth:`to_dict` output."""

        parameters = tuple(
            ParameterDescriptor.from_dict(item)
            for item in data.get("parameters", [])
        )
        metadata = dict(data.get("metadata") or {})
        return cls(parameters=parameters, metadata=metadata)


@dataclass(slots=True)
class GeneratedArtifact:
    """Describe a build artifact produced by a customization session."""

    path: str
    label: str | None = None
    relationship: str = "output"
    asset_id: int | None = None
    content_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a serialisable mapping for persistence."""

        return {
            "path": self.path,
            "label": self.label,
            "relationship": self.relationship,
            "asset_id": self.asset_id,
            "content_type": self.content_type,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GeneratedArtifact":
        """Hydrate an artifact instance from stored state."""

        return cls(
            path=str(data.get("path", "")),
            label=data.get("label"),
            relationship=str(data.get("relationship", "output")),
            asset_id=data.get("asset_id"),
            content_type=data.get("content_type"),
        )


@dataclass(slots=True)
class CustomizerSession:
    """Capture the state for a customization workflow run."""

    base_asset_path: str
    schema: ParameterSchema
    parameters: dict[str, Any]
    command: Sequence[str]
    artifacts: tuple[GeneratedArtifact, ...]
    session_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the session for persistence."""

        return {
            "base_asset_path": self.base_asset_path,
            "schema": self.schema.to_dict(),
            "parameters": dict(self.parameters),
            "command": list(self.command),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        session_id: int | None = None,
    ) -> "CustomizerSession":
        """Reconstruct a session from :meth:`to_dict` output."""

        schema_data = data.get("schema") or {}
        artifacts = tuple(
            GeneratedArtifact.from_dict(item)
            for item in data.get("artifacts", [])
        )
        command = tuple(str(item) for item in data.get("command", ()))
        return cls(
            base_asset_path=str(data.get("base_asset_path", "")),
            schema=ParameterSchema.from_dict(schema_data),
            parameters=dict(data.get("parameters") or {}),
            command=command,
            artifacts=artifacts,
            session_id=session_id,
            metadata=dict(data.get("metadata") or {}),
        )


class CustomizerBackend(Protocol):
    """Common interface implemented by customization backends."""

    name: str

    def load_schema(self, source: Path) -> ParameterSchema:
        """Inspect *source* and return a :class:`ParameterSchema`."""

    def validate(
        self,
        schema: ParameterSchema,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate *values* against *schema* returning normalized data."""

    def plan_build(
        self,
        source: Path,
        schema: ParameterSchema,
        values: Mapping[str, Any],
        *,
        output_dir: Path,
        asset_service: "AssetService | None" = None,
        execute: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> CustomizerSession:
        """Return a :class:`CustomizerSession` describing the build plan."""


if TYPE_CHECKING:  # pragma: no cover - typing helpers
    from ..storage import AssetService
