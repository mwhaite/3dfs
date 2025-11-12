"""OpenSCAD customizer backend."""

from __future__ import annotations

import ast
import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import (
    CustomizerBackend,
    CustomizerSession,
    GeneratedArtifact,
    ParameterDescriptor,
    ParameterSchema,
)

_RANGE_PATTERN = re.compile(
    r"^\s*([-+]?[0-9]*\.?[0-9]+)\s*:\s*([-+]?[0-9]*\.?[0-9]+)(?:\s*:\s*([-+]?[0-9]*\.?[0-9]+))?\s*$"
)
_ASSIGNMENT_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>[^;]+);(?:\s*//\s*(?P<comment>.*))?$"
)


if TYPE_CHECKING:  # pragma: no cover - typing helpers
    from ..storage import AssetService


class OpenSCADBackend(CustomizerBackend):
    """Parse OpenSCAD sources and emit build plans."""

    name = "openscad"

    def __init__(self, *, executable: str = "openscad") -> None:
        self.executable = executable

    # ------------------------------------------------------------------
    # Schema extraction
    # ------------------------------------------------------------------
    def load_schema(self, source: Path) -> ParameterSchema:
        """Return the parameter schema extracted from *source*."""

        if not source.exists():
            raise FileNotFoundError(f"Source file not found: {source}")
        text = source.read_text(encoding="utf-8")
        descriptors: list[ParameterDescriptor] = []
        for raw_line in text.splitlines():
            descriptor = self._parse_descriptor(raw_line)
            if descriptor is not None:
                descriptors.append(descriptor)
        metadata = {"backend": self.name, "source": str(source)}
        return ParameterSchema(parameters=tuple(descriptors), metadata=metadata)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(
        self,
        schema: ParameterSchema,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate *values* against *schema* returning normalized data."""

        normalized: dict[str, Any] = {}
        descriptors = {descriptor.name: descriptor for descriptor in schema.parameters}
        unknown = sorted(set(values) - set(descriptors))
        if unknown:
            raise ValueError(f"Unknown parameters: {', '.join(unknown)}")

        for descriptor in schema.parameters:
            value = values.get(descriptor.name, descriptor.default)
            normalized[descriptor.name] = self._normalize_value(descriptor, value)

        return normalized

    # ------------------------------------------------------------------
    # Build planning
    # ------------------------------------------------------------------
    def plan_build(
        self,
        source: Path,
        schema: ParameterSchema,
        values: Mapping[str, Any],
        *,
        output_dir: Path,
        asset_service: AssetService | None = None,
        execute: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> CustomizerSession:
        """Return a build plan for *source* applying *values*."""

        normalized = self.validate(schema, values)
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = output_dir / f"{source.stem}.stl"
        artifact = GeneratedArtifact(
            path=str(output_path),
            label=output_path.name,
            relationship="output",
            content_type="model/stl",
        )
        customized_source_path = output_dir / f"{source.stem}_customized.scad"
        try:
            updated_source = self._render_customized_source(source, normalized)
            customized_source_path.write_text(updated_source, encoding="utf-8")
        except Exception:
            customized_source_path = None

        artifacts: list[GeneratedArtifact] = [artifact]
        if customized_source_path is not None:
            artifacts.append(
                GeneratedArtifact(
                    path=str(customized_source_path),
                    label=customized_source_path.name,
                    relationship="source",
                    content_type="text/x-openscad",
                )
            )

        command: list[str] = [self.executable, "-o", str(output_path)]
        for name in sorted(normalized):
            command.extend(["-D", f"{name}={self._format_override(normalized[name])}"])
        command.append(str(source))

        session_metadata = {"backend": self.name, "executable": self.executable}
        if metadata:
            session_metadata.update(metadata)

        session = CustomizerSession(
            base_asset_path=str(source),
            schema=schema,
            parameters=normalized,
            command=tuple(command),
            artifacts=tuple(artifacts),
            metadata=session_metadata,
        )

        if execute:
            subprocess.run(command, check=True)

        return session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _parse_descriptor(self, line: str) -> ParameterDescriptor | None:
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            return None
        match = _ASSIGNMENT_PATTERN.match(stripped)
        if not match:
            return None

        name = match.group("name")
        value_str = match.group("value").strip()
        comment = match.group("comment") or ""

        default = self._parse_value(value_str)
        description, annotation = self._split_comment(comment)
        descriptor_kwargs = self._descriptor_from_annotation(name, default, annotation)
        if descriptor_kwargs is None:
            descriptor_kwargs = self._default_descriptor(name, default)
        if description and not descriptor_kwargs.get("description"):
            descriptor_kwargs["description"] = description
        return ParameterDescriptor(**descriptor_kwargs)

    def _parse_value(self, raw: str) -> Any:
        text = raw.strip()
        if text.lower() in {"true", "false"}:
            return text.lower() == "true"
        try:
            return ast.literal_eval(text)
        except Exception:
            return text

    def _split_comment(self, comment: str) -> tuple[str | None, str | None]:
        comment = comment.strip()
        if not comment:
            return None, None
        if "[" in comment and "]" in comment:
            prefix, _, remainder = comment.partition("[")
            annotation = remainder.split("]", 1)[0]
            annotation_text = f"[{annotation}]"
            description = prefix.strip() or None
            return description, annotation_text
        return comment, None

    def _descriptor_from_annotation(
        self,
        name: str,
        default: Any,
        annotation: str | None,
    ) -> dict[str, Any] | None:
        if not annotation:
            return None
        body = annotation.strip()
        match = _RANGE_PATTERN.match(body[1:-1] if body.startswith("[") else body)
        if match:
            first, second, third = match.groups()
            convert = int if isinstance(default, int) and not isinstance(default, bool) else float
            minimum = convert(float(first))
            if third is not None:
                step = convert(float(second))
                maximum = convert(float(third))
            else:
                step = None
                maximum = convert(float(second))
            coerced_default = self._coerce_number(default, convert)
            return {
                "name": name,
                "kind": "range",
                "default": coerced_default,
                "minimum": minimum,
                "maximum": maximum,
                "step": step,
            }
        try:
            literal = ast.literal_eval(body)
        except Exception:
            literal = None
        if isinstance(literal, list | tuple):
            choices = tuple(literal)
            coerced_default = default
            if choices and default not in choices:
                coerced_default = choices[0]
            return {
                "name": name,
                "kind": "choice",
                "default": coerced_default,
                "choices": choices,
            }
        return None

    def _default_descriptor(self, name: str, default: Any) -> dict[str, Any]:
        if isinstance(default, bool):
            return {"name": name, "kind": "boolean", "default": bool(default)}
        if isinstance(default, int) and not isinstance(default, bool):
            return {"name": name, "kind": "number", "default": int(default)}
        if isinstance(default, float):
            return {"name": name, "kind": "number", "default": float(default)}
        return {"name": name, "kind": "string", "default": str(default)}

    def _coerce_number(self, value: Any, convert) -> Any:
        try:
            return convert(value)
        except (TypeError, ValueError):
            return convert(float(value))

    def _normalize_value(self, descriptor: ParameterDescriptor, value: Any) -> Any:
        kind = descriptor.kind
        if kind == "boolean":
            return self._normalize_boolean(descriptor, value)
        if kind in {"number", "range"}:
            return self._normalize_number(descriptor, value)
        if kind == "choice":
            return self._normalize_choice(descriptor, value)
        if kind == "string":
            return str(value)
        return value

    def _normalize_boolean(self, descriptor: ParameterDescriptor, value: Any) -> bool:
        if isinstance(value, bool):
            result = value
        elif isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                result = True
            elif lowered in {"false", "0", "no", "off"}:
                result = False
            else:
                raise ValueError(f"Invalid boolean value for {descriptor.name}: {value!r}")
        else:
            raise ValueError(f"Invalid boolean value for {descriptor.name}: {value!r}")
        return bool(result)

    def _normalize_number(self, descriptor: ParameterDescriptor, value: Any) -> Any:
        target_type = int if isinstance(descriptor.default, int) and not isinstance(descriptor.default, bool) else float
        try:
            converted = target_type(value)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid numeric value for {descriptor.name}: {value!r}") from None

        minimum = descriptor.minimum
        maximum = descriptor.maximum
        if minimum is not None and converted < minimum:
            raise ValueError(f"Value for {descriptor.name} below minimum {minimum}: {converted}")
        if maximum is not None and converted > maximum:
            raise ValueError(f"Value for {descriptor.name} above maximum {maximum}: {converted}")
        return converted

    def _normalize_choice(self, descriptor: ParameterDescriptor, value: Any) -> Any:
        if value in descriptor.choices:
            return value
        if isinstance(value, str):
            for option in descriptor.choices:
                if isinstance(option, str) and option.lower() == value.lower():
                    return option
        raise ValueError(f"Invalid choice for {descriptor.name}: {value!r}, " f"expected one of {descriptor.choices}")

    def _format_override(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int | float):
            return str(value)
        return json.dumps(value)

    def _render_customized_source(self, source: Path, normalized: Mapping[str, Any]) -> str:
        text = source.read_text(encoding="utf-8")
        lines = text.splitlines()
        rendered: list[str] = []
        for line in lines:
            replacement = self._rewrite_assignment(line, normalized)
            rendered.append(replacement)
        # Preserve trailing newline if present in original source
        newline = "\n" if text.endswith("\n") else ""
        body = "\n".join(rendered)
        header = (
            "// Customized using three-dfs OpenSCAD backend.\n"
            "// Parameter overrides have been baked into this source.\n"
        )
        if text.lstrip().startswith("// Customized using three-dfs OpenSCAD backend."):
            return body + newline
        return header + body + newline

    def _rewrite_assignment(self, line: str, overrides: Mapping[str, Any]) -> str:
        stripped = line.strip()
        if not stripped:
            return line
        match = _ASSIGNMENT_PATTERN.match(stripped)
        if not match:
            return line

        name = match.group("name")
        if name not in overrides:
            return line

        comment = match.group("comment")
        indent_length = len(line) - len(line.lstrip(" \t"))
        indent = line[:indent_length]
        formatted = self._format_override(overrides[name])
        updated = f"{indent}{name} = {formatted};"
        if comment:
            updated += f" // {comment}"
        return updated
