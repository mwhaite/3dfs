"""Tests covering project scanning and attachment routing helpers."""

from __future__ import annotations

from pathlib import Path

from three_dfs.application.main_window import (
    _is_readme_candidate,
    _resolve_attachment_directory,
)
from three_dfs.application.project_scanner import scan_project_folder
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage


def _make_service(tmp_path: Path) -> AssetService:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    return AssetService(repository)


def test_scan_project_folder_marks_primary_components(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    project_root = tmp_path / "project"
    part_dir = project_root / "wing"
    part_dir.mkdir(parents=True)
    part_model = part_dir / "wing.stl"
    part_model.write_text("solid wing")

    outcome = scan_project_folder(project_root, service, existing=None)
    metadata = outcome.asset.metadata or {}

    assert metadata.get("primary_components") == {"wing": "wing/wing.stl"}

    components = metadata.get("components") or []
    placeholders = [
        entry for entry in components if entry.get("kind") == "placeholder"
    ]
    assert placeholders and placeholders[0]["path"] == str(part_dir)
    placeholder_meta = placeholders[0].get("metadata", {})
    assert (
        placeholder_meta.get("primary_component_path") == str(part_model.resolve())
    )
    assert placeholder_meta.get("primary_component_rel_path") == "wing/wing.stl"
    component_entries = {
        entry.get("path"): entry.get("metadata", {})
        for entry in components
        if entry.get("kind") == "component"
    }
    assert str(part_model) in component_entries
    assert component_entries[str(part_model)].get("is_primary_component") is True


def test_scan_project_folder_preserves_primary_on_rescan(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    project_root = tmp_path / "project"
    part_dir = project_root / "assembly"
    part_dir.mkdir(parents=True)
    primary_model = part_dir / "assembly.stl"
    primary_model.write_text("solid primary")

    initial = scan_project_folder(project_root, service, existing=None)

    secondary_model = part_dir / "assembly_alt.stl"
    secondary_model.write_text("solid secondary")

    rescan = scan_project_folder(project_root, service, existing=initial.asset)

    metadata = rescan.asset.metadata or {}
    assert metadata.get("primary_components") == {
        "assembly": "assembly/assembly.stl"
    }

    components = metadata.get("components") or []
    placeholders = [
        entry for entry in components if entry.get("kind") == "placeholder"
    ]
    assert placeholders and placeholders[0]["path"] == str(part_dir)
    placeholder_meta = placeholders[0].get("metadata", {})
    assert (
        placeholder_meta.get("primary_component_path")
        == str(primary_model.resolve())
    )
    assert placeholder_meta.get("primary_component_rel_path") == "assembly/assembly.stl"
    component_entries = {
        entry.get("path"): entry.get("metadata", {})
        for entry in components
        if entry.get("kind") == "component"
    }
    assert component_entries[str(primary_model)].get("is_primary_component") is True
    assert component_entries[str(secondary_model)].get("is_primary_component") is not True


def test_resolve_attachment_directory_prefers_selected_placeholder(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    part_dir = project_root / "gear"
    part_dir.mkdir(parents=True)

    resolved = _resolve_attachment_directory(
        project_root, (str(part_dir), "placeholder")
    )

    assert resolved == part_dir.resolve()


def test_resolve_attachment_directory_falls_back_for_external_path(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    external = tmp_path / "outside"
    external.mkdir()

    resolved = _resolve_attachment_directory(
        project_root, (str(external), "placeholder")
    )

    assert resolved == project_root.resolve()


def test_is_readme_candidate_variants() -> None:
    assert _is_readme_candidate(Path("README"))
    assert _is_readme_candidate(Path("readme.md"))
    assert not _is_readme_candidate(Path("notes.txt"))
