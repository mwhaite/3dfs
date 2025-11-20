from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from three_dfs.ui import model_viewer
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


def test_load_mesh_data_supports_three_mf(monkeypatch, tmp_path):
    mesh_path = tmp_path / "fixture.3mf"
    mesh_path.write_text("placeholder", encoding="utf-8")

    vertices = np.array(
        [
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
        ],
        dtype=np.float32,
    )
    faces = np.array([[0, 1, 2]], dtype=np.int32)

    monkeypatch.setattr(model_viewer, "trimesh", object())

    called: dict[str, Path] = {}

    def fake_loader(path: Path) -> tuple[np.ndarray, np.ndarray]:
        called["path"] = path
        return vertices, faces

    monkeypatch.setattr(model_viewer, "_load_with_trimesh_mesh", fake_loader)

    mesh, error = model_viewer.load_mesh_data(mesh_path)

    assert called["path"] == mesh_path
    assert error is None
    assert mesh is not None
    np.testing.assert_allclose(mesh.vertices, vertices)
    assert mesh.indices.dtype == np.uint32
    assert mesh.indices.tolist() == [0, 1, 2]


def test_load_mesh_data_supports_gcode():
    fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "sample_toolpath.gcode"

    mesh, error = model_viewer.load_mesh_data(fixture_path)

    assert error is None
    assert mesh is not None
    assert mesh.vertices.ndim == 2 and mesh.vertices.shape[1] == 3
    assert mesh.indices.ndim == 1
    assert mesh.indices.size > 0


def test_nc_gcode_shows_text_preview(qapp, tmp_path):
    file_path = tmp_path / "program.nc"
    file_path.write_text("G1 X0 Y0 F1200\n", encoding="utf-8")

    preview = PreviewPane(base_path=tmp_path)
    outcome = _apply_outcome_sync(preview, file_path)

    qapp.processEvents()

    assert outcome.text_role == "text"
    assert preview._tabs.isTabEnabled(preview._text_tab_index)


def test_pdf_preview_renders_first_page(qapp, tmp_path):
    file_path = tmp_path / "document.pdf"
    image = Image.new("RGB", (320, 320), color="white")
    drawer = ImageDraw.Draw(image)
    drawer.rectangle((24, 24, 296, 296), outline="black", width=6)
    drawer.text((120, 150), "PDF", fill="black")
    image.save(file_path, format="PDF")

    preview = PreviewPane(base_path=tmp_path)
    outcome = _apply_outcome_sync(preview, file_path)

    qapp.processEvents()

    assert ("Type", "PDF Document") in outcome.metadata
    assert outcome.thumbnail_bytes is not None
    assert outcome.thumbnail_bytes.startswith(b"\x89PNG")
    assert outcome.thumbnail_message is None

    preview.deleteLater()
