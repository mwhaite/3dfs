"""Tests for automated G-code preview rendering and caching."""

from __future__ import annotations

from pathlib import Path

import pytest

from three_dfs.gcode import (
    GCodePreviewCache,
    GCodePreviewError,
    analyze_gcode_program,
    extract_render_hints,
)
from three_dfs.importer import GCODE_EXTENSIONS
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage


@pytest.fixture()
def sample_gcode_path(tmp_path: Path) -> Path:
    target = tmp_path / "sample.gcode"
    source = Path(__file__).parent / "fixtures" / "sample_toolpath.gcode"
    target.write_text(source.read_text())
    return target


def test_analyze_gcode_program_detects_motion(sample_gcode_path: Path) -> None:
    analysis = analyze_gcode_program(sample_gcode_path)
    assert analysis.total_moves == 5
    assert analysis.cutting_moves == 4
    assert analysis.rapid_moves == 1
    assert analysis.units == "mm"
    min_x, _, max_x, _ = analysis.bounds_xy
    assert pytest.approx(min_x) == 0.0
    assert pytest.approx(max_x) == 40.0


def test_analyze_gcode_without_motion(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty.gcode"
    empty_path.write_text("G21\nG90\nM30\n")
    with pytest.raises(GCodePreviewError):
        analyze_gcode_program(empty_path)


def test_extract_render_hints_from_tags() -> None:
    tags = [
        "GCodeHint:tool=EndMill",
        "GCodeHint:Workpiece=80x80",
        "GCodeHint:cut_color=#ff0000",
        "notes",
    ]
    hints = extract_render_hints(tags)
    assert hints["tool"] == "EndMill"
    assert hints["workpiece"] == "80x80"
    assert hints["cut_color"] == "#ff0000"
    assert "notes" not in hints


def test_gcode_preview_cache_generates_and_reuses(tmp_path: Path, sample_gcode_path: Path) -> None:
    cache_root = tmp_path / "gcode_cache"
    cache = GCodePreviewCache(cache_root)
    analysis = analyze_gcode_program(sample_gcode_path)

    first = cache.get_or_render(sample_gcode_path, hints={"tool": "EndMill"}, analysis=analysis)
    assert first.updated is True
    assert first.path.exists()
    assert first.image_bytes.startswith(b"\x89PNG")
    assert first.info["hints"]["tool"] == "EndMill"

    second = cache.get_or_render(sample_gcode_path, hints={"tool": "EndMill"}, existing_info=first.info)
    assert second.updated is False
    assert second.image_bytes == first.image_bytes


def test_asset_service_generates_gcode_previews(tmp_path: Path, sample_gcode_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    cache = GCodePreviewCache(tmp_path / "cache")
    service = AssetService(repository, gcode_preview_cache=cache)

    assert sample_gcode_path.suffix.lower() in GCODE_EXTENSIONS

    record = service.create_asset(str(sample_gcode_path), label="toolpath", metadata={})
    service.add_tag(str(sample_gcode_path), "GCodeHint:tool=EndMill")
    service.add_tag(str(sample_gcode_path), "GCodeHint:material=Aluminium")

    asset = service.get_asset(record.id)
    assert asset is not None

    asset, preview = service.ensure_gcode_preview(asset)
    assert preview is not None
    assert preview.path.exists()
    info = asset.metadata.get("gcode_preview")
    assert isinstance(info, dict)
    assert info["hints"]["tool"] == "EndMill"
    assert "analysis" in info

    # Requesting again should reuse the cached image
    asset, second = service.ensure_gcode_preview(asset)
    assert second is not None
    assert second.updated is False

    generated = service.ensure_all_gcode_previews()
    assert generated == 0
