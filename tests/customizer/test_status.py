from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from three_dfs.customizer.status import evaluate_customization_status


def test_status_reports_up_to_date(tmp_path: Path) -> None:
    source = tmp_path / "fixture.scad"
    source.write_text("module test() {}\n", encoding="utf-8")

    recorded = datetime.fromtimestamp(source.stat().st_mtime, tz=UTC)
    metadata = {
        "base_asset_path": str(source),
        "source_modified_at": recorded.isoformat(),
    }

    status = evaluate_customization_status(metadata)

    assert status.base_path == source
    assert status.recorded_source_mtime == recorded
    assert status.current_source_mtime is not None
    assert status.is_outdated is False


def test_status_detects_outdated_source(tmp_path: Path) -> None:
    source = tmp_path / "fixture.scad"
    source.write_text("module test() {}\n", encoding="utf-8")

    recorded = datetime.fromtimestamp(source.stat().st_mtime, tz=UTC)
    metadata = {
        "base_asset_path": str(source),
        "source_modified_at": recorded.isoformat(),
    }

    newer = recorded + timedelta(seconds=30)
    os.utime(source, (newer.timestamp(), newer.timestamp()))

    status = evaluate_customization_status(metadata)

    assert status.is_outdated is True
    assert status.current_source_mtime is not None
    assert status.current_source_mtime > recorded


def test_status_handles_missing_source(tmp_path: Path) -> None:
    missing = tmp_path / "missing.scad"
    metadata = {
        "base_asset_path": str(missing),
        "source_modified_at": datetime.now(UTC).isoformat(),
    }

    status = evaluate_customization_status(metadata)

    assert status.is_outdated is True
    assert "missing" in status.reason.lower()


def test_status_allows_base_path_override(tmp_path: Path) -> None:
    source = tmp_path / "override.scad"
    source.write_text("module test() {}\n", encoding="utf-8")

    recorded = datetime.fromtimestamp(source.stat().st_mtime, tz=UTC)
    metadata = {"source_modified_at": recorded.isoformat()}

    status = evaluate_customization_status(metadata, base_path=source)

    assert status.base_path == source
    assert status.is_outdated is False
