"""Reusable mesh transformation utilities powered by build123d."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from math import isclose
from pathlib import Path
from typing import Any, ClassVar, TypeVar

try:  # pragma: no cover - optional dependency exercised in tests
    from build123d import (
        Align,
        BoundBox,
        Compound,
        Matrix,
        Mesher,
        Shape,
        Vector,
        export_stl,
    )
    from build123d.build_enums import Unit
except ImportError:  # pragma: no cover - dependency availability varies
    Align = BoundBox = Compound = Matrix = Mesher = Shape = Vector = export_stl = None  # type: ignore[assignment]
    Unit = None  # type: ignore[assignment]
    _BUILD123D_AVAILABLE = False
else:  # pragma: no cover - exercised via tests
    _BUILD123D_AVAILABLE = True

__all__ = [
    "TransformationDescriptor",
    "ScaleTransformation",
    "TranslateTransformation",
    "EmbossMeshTransformation",
    "EmbossTextTransformation",
    "BooleanUnionTransformation",
    "descriptor_from_dict",
    "serialise_descriptors",
    "apply_transformations",
]


_DescriptorType = TypeVar("_DescriptorType", bound="TransformationDescriptor")


class TransformationError(RuntimeError):
    """Raised when a transformation cannot be applied."""


@dataclass(slots=True)
class _TransformationContext:
    """Execution context shared between transformation descriptors."""

    units: str


def _require_build123d() -> None:
    if not _BUILD123D_AVAILABLE:  # pragma: no cover - guarded by import checks
        raise TransformationError(
            "build123d is required to apply customizer transformations"
        )


def _vector(values: Iterable[float], *, components: int) -> tuple[float, ...]:
    sequence = tuple(float(value) for value in values)
    if len(sequence) != components:
        raise ValueError(f"Expected {components} components, received {sequence!r}")
    return sequence


def _format_scad_vector(values: Iterable[float]) -> str:
    components = [f"{float(value):.6g}" for value in values]
    return f"[{', '.join(components)}]"


def _translation_matrix(offset: Iterable[float]) -> Matrix:
    _require_build123d()
    x, y, z = _vector(offset, components=3)
    return Matrix(
        [
            [1.0, 0.0, 0.0, x],
            [0.0, 1.0, 0.0, y],
            [0.0, 0.0, 1.0, z],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )


def _scale_matrix(factors: Iterable[float]) -> Matrix:
    _require_build123d()
    sx, sy, sz = _vector(factors, components=3)
    return Matrix(
        [
            [sx, 0.0, 0.0, 0.0],
            [0.0, sy, 0.0, 0.0],
            [0.0, 0.0, sz, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )


def _ensure_shape(candidate: Any) -> Shape:
    if hasattr(candidate, "bounding_box"):
        return candidate
    if isinstance(candidate, Sequence):
        if not candidate:
            raise TransformationError("Boolean operation returned no geometry")
        if len(candidate) == 1:
            return candidate[0]
        _require_build123d()
        return Compound(candidate)
    raise TransformationError("Unsupported geometry result from boolean operation")


def _combine_shapes(shapes: Sequence[Shape]) -> Shape:
    iterator = iter(shapes)
    try:
        combined = next(iterator)
    except StopIteration as exc:  # pragma: no cover - defensive guard
        raise TransformationError("No shapes were loaded from the mesh") from exc

    for shape in iterator:
        combined = _ensure_shape(combined.fuse(shape))

    return combined


def _shape_units(mesher: Mesher) -> str:
    unit = getattr(mesher, "unit", None)
    if unit is None:
        return "unspecified"
    if isinstance(unit, Unit):
        return unit.name.lower()
    return str(unit)


def _normalise_coordinate(value: float) -> float:
    """Normalise *value* to remove numerical noise from CAD operations."""

    # Initial rounding trims insignificant floating point artefacts while
    # preserving fine detail for legitimate measurements (e.g. 0.123456).
    cleaned = float(round(value, 9))
    if isclose(cleaned, 0.0, abs_tol=1e-9):
        return 0.0

    # Snap coordinates that lie extremely close to typical decimal grid points
    # (1/10th, 1/100th, etc.).  Boolean operations on tessellated geometry can
    # leave the bounding box short by a few hundred microns which causes the
    # metadata to under-report extents.  Iterating from finer to coarser grids
    # ensures we only snap when the adjustment is negligible.
    for decimals in range(4, 0, -1):
        candidate = round(cleaned, decimals)
        tolerance = 5 * 10 ** (-(decimals + 2))
        if abs(cleaned - candidate) <= tolerance:
            cleaned = float(candidate)

    return cleaned


def _shape_metadata(shape: Shape, *, units: str) -> dict[str, Any]:
    bbox: BoundBox = shape.bounding_box()
    min_corner = (
        _normalise_coordinate(float(bbox.min.X)),
        _normalise_coordinate(float(bbox.min.Y)),
        _normalise_coordinate(float(bbox.min.Z)),
    )
    max_corner = (
        _normalise_coordinate(float(bbox.max.X)),
        _normalise_coordinate(float(bbox.max.Y)),
        _normalise_coordinate(float(bbox.max.Z)),
    )
    vertices = shape.vertices()
    faces = shape.faces()
    return {
        "vertex_count": len(vertices),
        "face_count": len(faces),
        "bounding_box_min": [float(value) for value in min_corner],
        "bounding_box_max": [float(value) for value in max_corner],
        "units": units,
    }


def _load_shape(path: Path) -> tuple[Shape, str]:
    _require_build123d()
    mesher = Mesher()
    shapes = mesher.read(str(path))
    if not shapes:
        raise TransformationError(f"Unable to load mesh from {path!s}")
    return _combine_shapes(shapes), _shape_units(mesher)


def _apply_matrix(shape: Shape, matrix: Matrix) -> Shape:
    return shape.transform_geometry(matrix)


def _fuse_shapes(base: Shape, additions: Sequence[Shape]) -> Shape:
    combined = base
    for addition in additions:
        combined = _ensure_shape(combined.fuse(addition))
    return combined


def _create_text_shape(
    text: str,
    *,
    height: float,
    depth: float,
    font: str | None = None,
    spacing: float = 1.0,
) -> Shape:
    _require_build123d()
    if not text:
        raise ValueError("text must not be empty")
    outline = Compound.make_text(
        txt=text,
        font_size=height,
        font=font or "Arial",
        align=(Align.CENTER, Align.CENTER),
    )
    faces = outline.faces()
    if not faces:
        raise TransformationError("Text outline did not contain any faces")

    extrusion_direction = Vector(0.0, 0.0, 1.0)
    extrusions = [
        face.extrude(amount=depth, direction=extrusion_direction) for face in faces
    ]
    solid = extrusions[0] if len(extrusions) == 1 else Compound(extrusions)
    solid = _apply_matrix(solid, _translation_matrix((0.0, 0.0, -depth / 2.0)))
    if spacing != 1.0:
        solid = _apply_matrix(solid, _scale_matrix((spacing, 1.0, 1.0)))
    return solid


class TransformationDescriptor(ABC):
    """Base class describing a mesh transformation."""

    operation: ClassVar[str]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["operation"] = self.operation
        return payload

    @classmethod
    def from_dict(
        cls: type[_DescriptorType], data: Mapping[str, Any]
    ) -> _DescriptorType:
        operation = data.get("operation")
        if not operation:
            raise ValueError("Transformation descriptor missing 'operation'")

        descriptor_cls = _TRANSFORMATION_REGISTRY.get(str(operation))
        if descriptor_cls is None:
            raise ValueError(f"Unsupported transformation operation: {operation!r}")

        return descriptor_cls._from_dict(data)

    @classmethod
    @abstractmethod
    def _from_dict(
        cls: type[_DescriptorType], data: Mapping[str, Any]
    ) -> _DescriptorType:
        raise NotImplementedError

    @abstractmethod
    def apply(
        self, shape: Shape, context: _TransformationContext
    ) -> tuple[Shape, dict[str, Any]]:
        """Return a transformed shape and associated metadata."""

    def parameter_dict(self) -> dict[str, Any]:
        payload = self.to_dict().copy()
        payload.pop("operation", None)
        return payload

    def openscad_module(self, previous: str, index: int) -> tuple[str, str]:
        """Return an OpenSCAD module definition and its exported name."""

        return "", previous


_TRANSFORMATION_REGISTRY: dict[str, type[TransformationDescriptor]] = {}


def _register_descriptor(
    cls: type[_DescriptorType],
) -> type[_DescriptorType]:
    _TRANSFORMATION_REGISTRY[cls.operation] = cls
    return cls


@dataclass(slots=True)
@_register_descriptor
class ScaleTransformation(TransformationDescriptor):
    """Uniform or axis-aligned scaling of a shape."""

    factors: tuple[float, float, float]
    origin: tuple[float, float, float] | None = None

    operation: ClassVar[str] = "scale"

    @classmethod
    def _from_dict(cls, data: Mapping[str, Any]) -> ScaleTransformation:
        factors = _vector(data.get("factors", (1.0, 1.0, 1.0)), components=3)
        origin_data = data.get("origin")
        origin = None
        if origin_data is not None:
            origin = _vector(origin_data, components=3)
        return cls(factors=factors, origin=origin)

    def apply(
        self, shape: Shape, context: _TransformationContext
    ) -> tuple[Shape, dict[str, Any]]:
        _require_build123d()

        transformed = shape
        if self.origin is not None:
            inverse_origin = tuple(-value for value in self.origin)
            transformed = _apply_matrix(
                transformed, _translation_matrix(inverse_origin)
            )
        transformed = _apply_matrix(transformed, _scale_matrix(self.factors))
        if self.origin is not None:
            transformed = _apply_matrix(transformed, _translation_matrix(self.origin))

        metadata = {
            "operation": self.operation,
            "parameters": self.parameter_dict(),
            **_shape_metadata(transformed, units=context.units),
        }
        return transformed, metadata

    def openscad_module(self, previous: str, index: int) -> tuple[str, str]:
        module_name = f"op{index}"
        lines = [f"module {module_name}() {{"]
        indent = "    "
        if self.origin is not None:
            lines.append(f"{indent}translate({_format_scad_vector(self.origin)}) {{")
            indent += "    "
        lines.append(f"{indent}scale({_format_scad_vector(self.factors)}) {{")
        indent += "    "
        if self.origin is not None:
            inverse = tuple(-value for value in self.origin)
            lines.append(f"{indent}translate({_format_scad_vector(inverse)}) {{")
            indent += "    "
        lines.append(f"{indent}{previous}();")
        if self.origin is not None:
            indent = indent[:-4]
            lines.append(f"{indent}}}")
        indent = indent[:-4]
        lines.append(f"{indent}}}")
        if self.origin is not None:
            indent = indent[:-4]
            lines.append(f"{indent}}}")
        lines.append("}")
        return "\n".join(lines), module_name


@dataclass(slots=True)
@_register_descriptor
class TranslateTransformation(TransformationDescriptor):
    """Translate a shape by the given offset."""

    offset: tuple[float, float, float]

    operation: ClassVar[str] = "translate"

    @classmethod
    def _from_dict(cls, data: Mapping[str, Any]) -> TranslateTransformation:
        offset = _vector(data.get("offset", (0.0, 0.0, 0.0)), components=3)
        return cls(offset=offset)

    def apply(
        self, shape: Shape, context: _TransformationContext
    ) -> tuple[Shape, dict[str, Any]]:
        _require_build123d()
        transformed = _apply_matrix(shape, _translation_matrix(self.offset))
        metadata = {
            "operation": self.operation,
            "parameters": self.parameter_dict(),
            **_shape_metadata(transformed, units=context.units),
        }
        return transformed, metadata

    def openscad_module(self, previous: str, index: int) -> tuple[str, str]:
        module_name = f"op{index}"
        lines = [
            f"module {module_name}() {{",
            f"    translate({_format_scad_vector(self.offset)}) {{",
            f"        {previous}();",
            "    }",
            "}",
        ]
        return "\n".join(lines), module_name


@dataclass(slots=True)
@_register_descriptor
class EmbossMeshTransformation(TransformationDescriptor):
    """Emboss an external mesh onto the base geometry."""

    mesh_path: str
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] | float = 1.0

    operation: ClassVar[str] = "emboss_mesh"

    @classmethod
    def _from_dict(cls, data: Mapping[str, Any]) -> EmbossMeshTransformation:
        position = _vector(data.get("position", (0.0, 0.0, 0.0)), components=3)
        mesh_path = str(data.get("mesh_path", "")).strip()
        if not mesh_path:
            raise ValueError("mesh_path is required for emboss mesh transformations")
        scale_data = data.get("scale", 1.0)
        if isinstance(scale_data, int | float):
            scale: tuple[float, float, float] | float = float(scale_data)
        else:
            scale = _vector(scale_data, components=3)
        return cls(mesh_path=mesh_path, position=position, scale=scale)

    def _apply_scale(self, shape: Shape) -> Shape:
        factors = (
            (float(self.scale), float(self.scale), float(self.scale))
            if isinstance(self.scale, int | float)
            else self.scale
        )
        return _apply_matrix(shape, _scale_matrix(factors))

    def apply(
        self, shape: Shape, context: _TransformationContext
    ) -> tuple[Shape, dict[str, Any]]:
        _require_build123d()
        addition, addition_units = _load_shape(Path(self.mesh_path))
        addition = self._apply_scale(addition)
        addition = _apply_matrix(addition, _translation_matrix(self.position))
        combined = _fuse_shapes(shape, (addition,))
        metadata = {
            "operation": self.operation,
            "parameters": self.parameter_dict(),
            "component": _shape_metadata(addition, units=addition_units),
            **_shape_metadata(combined, units=context.units),
        }
        return combined, metadata

    def openscad_module(self, previous: str, index: int) -> tuple[str, str]:
        module_name = f"op{index}"
        if isinstance(self.scale, int | float):
            factors = (float(self.scale),) * 3
        else:
            factors = self.scale
        mesh = Path(self.mesh_path).as_posix()
        lines = [
            f"module {module_name}() {{",
            "    union() {",
            f"        {previous}();",
            "        translate({_format_scad_vector(self.position)}) {",
            f"            scale({_format_scad_vector(factors)}) {{",
            f'                import("{mesh}");',
            "            }",
            "        }",
            "    }",
            "}",
        ]
        return "\n".join(lines), module_name


@dataclass(slots=True)
@_register_descriptor
class EmbossTextTransformation(TransformationDescriptor):
    """Create and emboss 3D text onto the base shape."""

    text: str
    height: float = 1.0
    depth: float = 0.5
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    font: str | None = None
    spacing: float = 1.0

    operation: ClassVar[str] = "emboss_text"

    @classmethod
    def _from_dict(cls, data: Mapping[str, Any]) -> EmbossTextTransformation:
        text = str(data.get("text", "")).strip()
        if not text:
            raise ValueError("text is required for emboss text transformations")
        position = _vector(data.get("position", (0.0, 0.0, 0.0)), components=3)
        font = data.get("font")
        return cls(
            text=text,
            height=float(data.get("height", 1.0)),
            depth=float(data.get("depth", 0.5)),
            position=position,
            font=str(font) if font is not None else None,
            spacing=float(data.get("spacing", 1.0)),
        )

    def apply(
        self, shape: Shape, context: _TransformationContext
    ) -> tuple[Shape, dict[str, Any]]:
        _require_build123d()
        text_shape = _create_text_shape(
            self.text,
            height=self.height,
            depth=self.depth,
            font=self.font,
            spacing=self.spacing,
        )
        text_shape = _apply_matrix(text_shape, _translation_matrix(self.position))
        combined = _fuse_shapes(shape, (text_shape,))
        metadata = {
            "operation": self.operation,
            "parameters": self.parameter_dict(),
            "component": _shape_metadata(text_shape, units=context.units),
            **_shape_metadata(combined, units=context.units),
        }
        return combined, metadata

    def openscad_module(self, previous: str, index: int) -> tuple[str, str]:
        module_name = f"op{index}"
        font_clause = f', font="{self.font}"' if self.font else ""
        text_call = (
            f'text("{self.text}", size={self.height:.6g}{font_clause}, '
            f'halign="center", valign="center", spacing={self.spacing:.6g});'
        )
        lines = [
            f"module {module_name}() {{",
            "    union() {",
            f"        {previous}();",
            f"        translate({_format_scad_vector(self.position)}) {{",
            f"            linear_extrude(height={self.depth:.6g}) {{",
            f"                {text_call}",
            "            }",
            "        }",
            "    }",
            "}",
        ]
        return "\n".join(lines), module_name


@dataclass(slots=True)
@_register_descriptor
class BooleanUnionTransformation(TransformationDescriptor):
    """Boolean union between the base shape and external meshes."""

    mesh_paths: tuple[str, ...]

    operation: ClassVar[str] = "boolean_union"

    @classmethod
    def _from_dict(cls, data: Mapping[str, Any]) -> BooleanUnionTransformation:
        mesh_paths = tuple(str(path) for path in data.get("mesh_paths", ()))
        if not mesh_paths:
            raise ValueError("mesh_paths must contain at least one entry")
        return cls(mesh_paths=mesh_paths)

    def apply(
        self, shape: Shape, context: _TransformationContext
    ) -> tuple[Shape, dict[str, Any]]:
        _require_build123d()
        additions: list[Shape] = []
        components_metadata: list[dict[str, Any]] = []
        for path_str in self.mesh_paths:
            addition, addition_units = _load_shape(Path(path_str))
            additions.append(addition)
            components_metadata.append(_shape_metadata(addition, units=addition_units))
        combined = _fuse_shapes(shape, additions)
        metadata = {
            "operation": self.operation,
            "parameters": self.parameter_dict(),
            "components": components_metadata,
            **_shape_metadata(combined, units=context.units),
        }
        return combined, metadata

    def openscad_module(self, previous: str, index: int) -> tuple[str, str]:
        module_name = f"op{index}"
        lines = [
            f"module {module_name}() {{",
            "    union() {",
            f"        {previous}();",
        ]
        for mesh_path in self.mesh_paths:
            lines.append(f'        import("{Path(mesh_path).as_posix()}");')
        lines.append("    }")
        lines.append("}")
        return "\n".join(lines), module_name


def descriptor_from_dict(data: Mapping[str, Any]) -> TransformationDescriptor:
    """Hydrate a descriptor instance from stored metadata."""

    return TransformationDescriptor.from_dict(data)


def serialise_descriptors(
    descriptors: Sequence[TransformationDescriptor],
) -> list[dict[str, Any]]:
    """Return a JSON-serialisable representation of *descriptors*."""

    return [descriptor.to_dict() for descriptor in descriptors]


def _normalise_descriptors(
    descriptors: Sequence[TransformationDescriptor | Mapping[str, Any]],
) -> list[TransformationDescriptor]:
    normalised: list[TransformationDescriptor] = []
    for descriptor in descriptors:
        if isinstance(descriptor, TransformationDescriptor):
            normalised.append(descriptor)
        else:
            normalised.append(descriptor_from_dict(descriptor))
    return normalised


def _export_shape(
    shape: Shape,
    target_path: Path,
    export_kwargs: Mapping[str, Any] | None,
) -> None:
    suffix = target_path.suffix.lower()
    if suffix != ".stl":
        raise TransformationError(
            f"Unsupported export format '{suffix or 'unknown'}'; only STL is supported"
        )

    parameters = dict(export_kwargs or {})
    tolerance = float(parameters.pop("tolerance", 1e-3))
    angular_tolerance = float(parameters.pop("angular_tolerance", 0.1))
    ascii_format = bool(parameters.pop("ascii_format", False))
    if parameters:
        unknown = ", ".join(sorted(parameters))
        raise TransformationError(f"Unsupported export parameters: {unknown}")

    export_stl(
        shape,
        str(target_path),
        tolerance=tolerance,
        angular_tolerance=angular_tolerance,
        ascii_format=ascii_format,
    )


def _initial_scad_module(source: Path) -> tuple[str, str]:
    module_name = "op0"
    lines = [
        f"module {module_name}() {{",
        f'    import("{source.as_posix()}");',
        "}",
    ]
    return "\n".join(lines), module_name


def apply_transformations(
    source: str | Path,
    transformations: Sequence[TransformationDescriptor | Mapping[str, Any]],
    *,
    output_path: str | Path,
    export_kwargs: Mapping[str, Any] | None = None,
    scad_output: str | Path | None = None,
) -> dict[str, Any]:
    """Apply *transformations* to *source* and persist the resulting mesh."""

    _require_build123d()

    source_path = Path(source)
    shape, units = _load_shape(source_path)
    context = _TransformationContext(units=units)

    operations_metadata: list[dict[str, Any]] = []
    current_shape = shape

    scad_modules: list[str] = []
    module_definition, previous_module = _initial_scad_module(source_path)
    scad_modules.append(module_definition)

    for index, descriptor in enumerate(
        _normalise_descriptors(transformations), start=1
    ):
        current_shape, metadata = descriptor.apply(current_shape, context)
        module_definition, module_name = descriptor.openscad_module(
            previous_module, index
        )
        if module_definition:
            scad_modules.append(module_definition)
            metadata["openscad_module"] = module_name
            previous_module = module_name
        operations_metadata.append(metadata)

    scad_modules.append(f"{previous_module}();")
    scad_script = "\n\n".join(scad_modules)

    output_target = Path(output_path)
    output_target.parent.mkdir(parents=True, exist_ok=True)
    _export_shape(current_shape, output_target, export_kwargs)

    if scad_output is not None:
        Path(scad_output).write_text(scad_script, encoding="utf-8")

    result_metadata = {
        "source": str(source_path),
        "output": str(output_target),
        "backend": "build123d",
        "operations": operations_metadata,
        "openscad_script": scad_script,
        **_shape_metadata(current_shape, units=context.units),
    }

    return result_metadata
