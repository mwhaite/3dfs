from __future__ import annotations

from pathlib import Path
import io

from PIL import Image

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from three_dfs.ui.preview_pane import (
    PreviewOutcome,
    PreviewPane,
    _build_preview_outcome,
)


def _apply_outcome_sync(preview: PreviewPane, path: Path) -> PreviewOutcome:
    outcome = _build_preview_outcome(path)
    preview._apply_outcome(outcome)
    return outcome


def test_plain_text_preview_shows_text_tab(qapp, tmp_path):
    file_path = tmp_path / "notes.txt"
    file_path.write_text("Hello preview!", encoding="utf-8")

    preview = PreviewPane(base_path=tmp_path)
    outcome = _apply_outcome_sync(preview, file_path)

    qapp.processEvents()

    assert outcome.text_role == "text"
    assert preview._tabs.isTabEnabled(preview._text_tab_index)
    assert preview._tabs.tabText(preview._text_tab_index) == "Text"
    assert "Hello preview!" in preview._text_view.toPlainText()
    assert preview._tabs.currentIndex() == preview._text_tab_index

    preview.deleteLater()


def test_scad_source_uses_openscad_tab(qapp, tmp_path):
    file_path = tmp_path / "part.scad"
    file_path.write_text("module demo() {}", encoding="utf-8")

    preview = PreviewPane(base_path=tmp_path)
    outcome = _apply_outcome_sync(preview, file_path)

    qapp.processEvents()

    assert outcome.text_role == "openscad"
    assert ("Kind", "OpenSCAD Source") in outcome.metadata
    assert preview._tabs.tabText(preview._text_tab_index) == "OpenSCAD"
    assert preview._tabs.isTabEnabled(preview._text_tab_index)

    preview.deleteLater()


def test_build123d_script_detected(qapp, tmp_path):
    file_path = tmp_path / "model.py"
    file_path.write_text("from build123d import BuildPart\nresult = BuildPart()\n", encoding="utf-8")

    preview = PreviewPane(base_path=tmp_path)
    outcome = _apply_outcome_sync(preview, file_path)

    qapp.processEvents()

    assert outcome.text_role == "build123d"
    assert ("Kind", "Build123D Script") in outcome.metadata
    assert preview._tabs.tabText(preview._text_tab_index) == "Build123D"
    assert preview._tabs.isTabEnabled(preview._text_tab_index)

    preview.deleteLater()


def test_chitubox_preview_extracted(qapp, tmp_path):
    preview_image = Image.new("RGB", (32, 24), (255, 0, 0))
    buffer = io.BytesIO()
    preview_image.save(buffer, format="PNG")

    file_path = tmp_path / "job.ctb"
    file_path.write_bytes(b"CTB" + buffer.getvalue() + b"\x00\x00")

    preview = PreviewPane(base_path=tmp_path)
    outcome = _apply_outcome_sync(preview, file_path)

    qapp.processEvents()

    assert outcome.thumbnail_bytes is not None
    assert ("Kind", "SLA Print") in outcome.metadata
    assert ("Type", "SLA Print (CTB)") in outcome.metadata
    assert preview._tabs.isTabEnabled(preview._thumbnail_tab_index)

    preview.deleteLater()
