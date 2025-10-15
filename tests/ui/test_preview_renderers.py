from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QSignalSpy

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
    file_path.write_text(
        "from build123d import BuildPart\nresult = BuildPart()\n", encoding="utf-8"
    )

    preview = PreviewPane(base_path=tmp_path)
    outcome = _apply_outcome_sync(preview, file_path)

    qapp.processEvents()

    assert outcome.text_role == "build123d"
    assert ("Kind", "Build123D Script") in outcome.metadata
    assert preview._tabs.tabText(preview._text_tab_index) == "Build123D"
    assert preview._tabs.isTabEnabled(preview._text_tab_index)

    preview.deleteLater()


def test_preview_carousel_image_navigation(qapp, tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()

    model_a = project_root / "model_a.stl"
    model_a.write_text("solid a", encoding="utf-8")
    model_b = project_root / "model_b.stl"
    model_b.write_text("solid b", encoding="utf-8")

    preview_a = project_root / "model_a.png"
    preview_b = project_root / "model_b.png"

    image = QImage(24, 24, QImage.Format_ARGB32)
    image.fill(QColor("red"))
    image.save(str(preview_a))
    image.fill(QColor("blue"))
    image.save(str(preview_b))

    metadata = {
        "project_path": str(project_root),
        "asset_path": "model_a.stl",
        "asset_label": "Model A",
        "preview_images": ["model_a.png"],
        "models": [
            {
                "path": "model_a.stl",
                "label": "Model A",
                "preview_images": ["model_a.png"],
            },
            {
                "path": "model_b.stl",
                "label": "Model B",
                "preview_images": ["model_b.png"],
            },
        ],
    }

    preview = PreviewPane(base_path=project_root)
    preview._enqueue_preview = lambda path: None
    preview._prepare_customizer = lambda path: None

    preview.set_item(
        "model_a.stl",
        label="Model A",
        metadata=metadata,
        asset_record=None,
    )

    qapp.processEvents()

    assert preview._image_gallery.count() == 2

    spy = QSignalSpy(preview.navigationRequested)
    preview._image_gallery.setCurrentRow(1)
    qapp.processEvents()

    assert spy.count() == 1
    expected_path = str(model_b.expanduser().resolve(strict=False))
    assert spy.at(0)[0] == expected_path

    preview.deleteLater()
