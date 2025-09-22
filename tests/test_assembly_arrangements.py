from __future__ import annotations

from pathlib import Path

from three_dfs.assembly import discover_arrangement_scripts


def _resolve(path: Path) -> str:
    return str(path.expanduser().resolve(strict=False))


def test_discover_arrangement_scripts_detects_layouts(tmp_path):
    assembly_dir = tmp_path / "airframe"
    arrangement_dir = assembly_dir / "arrangements"
    arrangement_dir.mkdir(parents=True)

    exploded = arrangement_dir / "exploded_view.scad"
    packed = arrangement_dir / "packed.scad"
    exploded.write_text("// exploded view")
    packed.write_text("// packed view")

    arrangements = discover_arrangement_scripts(assembly_dir)
    assert len(arrangements) == 2

    paths = {entry["path"] for entry in arrangements}
    assert _resolve(exploded) in paths
    assert _resolve(packed) in paths

    kinds = {entry.get("kind") for entry in arrangements}
    assert kinds == {"arrangement"}

    labels = {entry.get("label") for entry in arrangements}
    assert "Exploded View" in labels
    assert "Packed" in labels

    rel_paths = {entry.get("rel_path") for entry in arrangements}
    assert f"arrangements/{exploded.name}" in rel_paths
    assert f"arrangements/{packed.name}" in rel_paths


def test_discover_arrangement_scripts_preserves_existing_metadata(tmp_path):
    assembly_dir = tmp_path / "assembly"
    arrangement_dir = assembly_dir / "arrangements"
    arrangement_dir.mkdir(parents=True)

    exploded = arrangement_dir / "exploded_view.scad"
    exploded.write_text("// exploded")

    custom = assembly_dir / "custom_layout.scad"
    custom.write_text("// custom layout")

    existing = [
        {
            "path": _resolve(exploded),
            "label": "Manual Exploded",
            "description": "Exploded layout for documentation",
        },
        {
            "rel_path": "custom_layout.scad",
            "label": "Custom Layout",
            "metadata": {"variant": "A"},
        },
        {"path": str(assembly_dir / "missing.scad"), "label": "Missing"},
        {"path": "arrangements/legacy.scad", "label": "Legacy"},
    ]

    arrangements = discover_arrangement_scripts(assembly_dir, existing)
    by_path = {entry["path"]: entry for entry in arrangements}

    exploded_key = _resolve(exploded)
    assert exploded_key in by_path
    assert by_path[exploded_key]["label"] == "Manual Exploded"
    assert by_path[exploded_key]["description"] == "Exploded layout for documentation"

    custom_key = _resolve(custom)
    assert custom_key in by_path
    assert by_path[custom_key]["label"] == "Custom Layout"
    assert by_path[custom_key]["metadata"] == {"variant": "A"}

    for path in by_path:
        assert "missing.scad" not in path
        assert "legacy.scad" not in path
