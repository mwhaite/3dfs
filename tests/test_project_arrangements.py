from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from three_dfs.container import (
    build_attachment_metadata,
    build_component_metadata,
    discover_arrangement_scripts,
)
from three_dfs.storage.repository import AssetRecord


def _resolve(path: Path) -> str:
    return str(path.expanduser().resolve(strict=False))


def test_discover_arrangement_scripts_detects_layouts(tmp_path):
    project_dir = tmp_path / "airframe"
    arrangement_dir = project_dir / "arrangements"
    arrangement_dir.mkdir(parents=True)

    exploded = arrangement_dir / "exploded_view.scad"
    packed = arrangement_dir / "packed.scad"
    exploded.write_text("// exploded view")
    packed.write_text("// packed view")

    arrangements = discover_arrangement_scripts(project_dir)
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

    for entry in arrangements:
        metadata = entry.get("metadata")
        assert isinstance(metadata, dict)
        assert metadata.get("handler") == "openscad"
        assert metadata.get("container_path") == str(project_dir.resolve())


def test_discover_arrangement_scripts_preserves_existing_metadata(tmp_path):
    project_dir = tmp_path / "project"
    arrangement_dir = project_dir / "arrangements"
    arrangement_dir.mkdir(parents=True)

    exploded = arrangement_dir / "exploded_view.scad"
    exploded.write_text("// exploded")

    custom = project_dir / "custom_layout.scad"
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
        {"path": str(project_dir / "missing.scad"), "label": "Missing"},
        {"path": "arrangements/legacy.scad", "label": "Legacy"},
    ]

    arrangements = discover_arrangement_scripts(project_dir, existing)
    by_path = {entry["path"]: entry for entry in arrangements}

    exploded_key = _resolve(exploded)
    assert exploded_key in by_path
    assert by_path[exploded_key]["label"] == "Manual Exploded"
    assert by_path[exploded_key]["description"] == "Exploded layout for documentation"

    custom_key = _resolve(custom)
    assert custom_key in by_path
    assert by_path[custom_key]["label"] == "Custom Layout"
    custom_meta = by_path[custom_key]["metadata"]
    assert isinstance(custom_meta, dict)
    assert custom_meta.get("variant") == "A"

    for path in by_path:
        assert "missing.scad" not in path
        assert "legacy.scad" not in path


def _make_asset_record(base_path: Path) -> AssetRecord:
    now = datetime.now(UTC)
    part = base_path / "component.stl"
    record = AssetRecord(
        id=42,
        path=str(part),
        label="component.stl",
        metadata={"source": "https://example.com/component.stl", "creator": "Ada"},
        tags=[],
        created_at=now,
        updated_at=now,
    )
    return record


def test_build_component_metadata_includes_author_and_links(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    record = _make_asset_record(tmp_path)
    metadata = build_component_metadata(record, container_root=project_root)
    assert metadata["container_path"] == str(project_root)
    assert metadata["author"] == "Ada"
    links = metadata.get("upstream_links")
    assert isinstance(links, list)
    assert any(entry.get("url") == "https://example.com/component.stl" for entry in links)
    assert metadata.get("handler")


def test_build_attachment_metadata_sets_relationships(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    attachment = project_root / "notes.txt"
    attachment.write_text("hello")

    metadata = build_attachment_metadata(
        attachment,
        container_root=project_root,
        source_path=attachment,
    )
    assert metadata["container_path"] == str(project_root)
    assert metadata["asset_path"] == str(attachment)
    assert metadata.get("handler") == "system"
    related = metadata.get("related_items")
    assert isinstance(related, list) and related
    assert related[0].get("path") == str(project_root)


def test_build_attachment_metadata_merges_links_and_author(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    attachment = project_root / "notes.txt"
    attachment.write_text("hello")

    metadata = build_attachment_metadata(
        attachment,
        container_root=project_root,
        existing_metadata={
            "creator": "Grace Hopper",
            "source_url": "https://example.com/notes.txt",
            "upstream_links": [{"url": "https://existing.example", "label": "Existing"}],
        },
    )

    assert metadata["author"] == "Grace Hopper"
    links = metadata.get("upstream_links")
    assert isinstance(links, list)
    urls = {entry.get("url") for entry in links if isinstance(entry, dict)}
    assert "https://example.com/notes.txt" in urls
    assert "https://existing.example" in urls
