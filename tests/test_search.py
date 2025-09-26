"""Tests for the :mod:`three_dfs.search` helpers."""

from __future__ import annotations

from pathlib import Path

from three_dfs.search import LibrarySearch
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage


def _make_service(tmp_path: Path) -> AssetService:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    return AssetService(repository)


def test_search_matches_projects_components_and_attachments(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    project_root = tmp_path / "projects" / "starship"
    project_root.mkdir(parents=True)

    metadata = {
        "kind": "project",
        "project": "Starship",
        "components": [
            {
                "path": str(project_root / "wing" / "panel.stl"),
                "label": "Wing Panel",
                "kind": "component",
                "metadata": {
                    "description": "Primary lift surface",
                    "tags": ["aero", "wing"],
                },
                "asset_id": 101,
            },
            {
                "path": str(project_root / "electronics"),
                "label": "Electronics Bay",
                "kind": "placeholder",
                "metadata": {"notes": "Route wiring"},
            },
        ],
        "attachments": [
            {
                "path": str(project_root / "manuals" / "assembly.pdf"),
                "label": "Assembly Manual",
                "metadata": {"description": "Step by step instructions"},
            }
        ],
    }

    project = service.create_asset(
        str(project_root),
        label="Project: Starship",
        metadata=metadata,
        tags=["Space", "Build"],
    )

    search = LibrarySearch(service=service)

    # Components should match by label and metadata tokens.
    component_hits = search.search("wing")
    assert any(
        hit.scope == "component" and "label" in hit.matched_fields
        for hit in component_hits
    )

    # Attachment should match metadata text.
    attachment_hits = search.search("instructions", scopes=["attachment"])
    assert attachment_hits and attachment_hits[0].scope == "attachment"
    assert "metadata" in attachment_hits[0].matched_fields

    # Projects should match by parent tag text.
    project_hits = search.search("space", scopes=["project"])
    assert project_hits and project_hits[0].path == project.path
    assert "tags" in project_hits[0].matched_fields

    # Components inherit project tags during matching.
    inherited = search.search("build", scopes=["component"])
    assert inherited and any("tags" in hit.matched_fields for hit in inherited)


def test_search_supports_limits_and_scope_validation(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    service.create_asset("/tmp/part.stl", label="Part", metadata={}, tags=["Hardware"])

    search = LibrarySearch(service=service)

    limited = search.search("part", limit=1)
    assert len(limited) == 1

    try:
        search.search("part", scopes=["unknown"])  # type: ignore[arg-type]
    except ValueError as exc:
        assert "Unknown search scope" in str(exc)
    else:  # pragma: no cover - ensure failure is visible
        raise AssertionError("Expected ValueError for invalid scope")
