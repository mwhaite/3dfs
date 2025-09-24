from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

try:  # pragma: no cover - exercised when build123d is unavailable
    from build123d import (
        Align,
        Box,
        BuildPart,
        Cylinder,
        Sphere,
        Vector,
        export_stl,
    )
except ImportError:  # pragma: no cover - used in CI when dependency missing
    pytest.skip(
        "build123d is required for transformation tests", allow_module_level=True
    )

from three_dfs.customizer.transformations import (
    BooleanUnionTransformation,
    EmbossMeshTransformation,
    EmbossTextTransformation,
    ScaleTransformation,
    TranslateTransformation,
    apply_transformations,
    descriptor_from_dict,
    serialise_descriptors,
)


def _export_shape(shape, path: Path) -> Path:
    export_stl(shape, str(path))
    return path


@pytest.fixture()
def base_mesh_path(tmp_path: Path) -> Path:
    with BuildPart() as part:
        Box(1.0, 1.0, 1.0, align=Align.CENTER)
    return _export_shape(part.part, tmp_path / "base.stl")


def test_scale_and_translate(base_mesh_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "scaled_translated.stl"

    metadata = apply_transformations(
        base_mesh_path,
        [
            ScaleTransformation(factors=(2.0, 1.0, 0.5)),
            TranslateTransformation(offset=(1.0, -2.0, 0.25)),
        ],
        output_path=output,
    )

    assert output.exists()
    assert metadata["backend"] == "build123d"
    assert len(metadata["operations"]) == 2
    assert "scale([2" in metadata["openscad_script"]

    # After scaling the base cube (-0.5..0.5) we expect dimensions of
    # (-1..1, -0.5..0.5, -0.25..0.25). The translation shifts the bounds.
    expected_min = np.array([-1.0, -0.5, -0.25]) + np.array([1.0, -2.0, 0.25])
    expected_max = np.array([1.0, 0.5, 0.25]) + np.array([1.0, -2.0, 0.25])

    assert metadata["bounding_box_min"] == pytest.approx(expected_min.tolist())
    assert metadata["bounding_box_max"] == pytest.approx(expected_max.tolist())


def test_boolean_union_combines_meshes(base_mesh_path: Path, tmp_path: Path) -> None:
    with BuildPart() as part:
        Sphere(radius=0.4)
    sphere_shape = part.part.translate(Vector(1.2, 0.0, 0.0))
    sphere_path = _export_shape(sphere_shape, tmp_path / "sphere.stl")

    output = tmp_path / "union.stl"

    metadata = apply_transformations(
        base_mesh_path,
        [BooleanUnionTransformation(mesh_paths=(str(sphere_path),))],
        output_path=output,
    )

    assert output.exists()
    assert metadata["operations"][0]["operation"] == "boolean_union"

    bounding_min = np.array(metadata["bounding_box_min"])
    bounding_max = np.array(metadata["bounding_box_max"])

    # Ensure the union includes both the cube and the translated sphere.
    assert bounding_min[0] <= -0.5
    assert bounding_max[0] >= 1.6


def test_emboss_mesh_records_component_metadata(
    base_mesh_path: Path, tmp_path: Path
) -> None:
    with BuildPart() as part:
        Cylinder(
            radius=0.2, height=0.6, align=(Align.CENTER, Align.CENTER, Align.CENTER)
        )
    cylinder_path = _export_shape(part.part, tmp_path / "cylinder.stl")

    output = tmp_path / "emboss_mesh.stl"

    metadata = apply_transformations(
        base_mesh_path,
        [
            EmbossMeshTransformation(
                mesh_path=str(cylinder_path),
                position=(0.0, 0.0, 0.5),
                scale=(1.0, 1.0, 1.0),
            )
        ],
        output_path=output,
    )

    assert output.exists()

    operation = metadata["operations"][0]
    assert operation["operation"] == "emboss_mesh"
    assert "component" in operation
    assert operation["component"]["vertex_count"] > 0

    # The cylinder sits on top of the cube. Ensure the combined mesh grew along Z.
    assert metadata["bounding_box_max"][2] > 0.5


def test_emboss_text_generates_geometry(base_mesh_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "emboss_text.stl"

    depth = 0.2
    # Place the text so it protrudes above the cube by half its depth.
    z_position = 0.5 + depth / 2.0

    metadata = apply_transformations(
        base_mesh_path,
        [
            EmbossTextTransformation(
                text="A",
                height=0.5,
                depth=depth,
                position=(0.0, 0.0, z_position),
            )
        ],
        output_path=output,
    )

    assert output.exists()

    operation = metadata["operations"][0]
    assert operation["operation"] == "emboss_text"
    assert operation["component"]["vertex_count"] > 0
    assert metadata["bounding_box_max"][2] == pytest.approx(z_position + depth / 2.0)


def test_descriptor_serialisation_round_trip() -> None:
    descriptors = [
        ScaleTransformation(factors=(1.5, 1.0, 1.0)),
        TranslateTransformation(offset=(0.0, 0.0, 1.0)),
        EmbossTextTransformation(text="Hello", height=0.3, depth=0.1),
    ]

    payload = serialise_descriptors(descriptors)
    hydrated = [descriptor_from_dict(item) for item in payload]

    assert [item.to_dict() for item in hydrated] == payload
