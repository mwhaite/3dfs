"""Tests exercising the project pane navigation helpers."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("PySide6.QtCore", exc_type=ImportError)
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import Qt

from three_dfs.ui.project_pane import ProjectComponent, ProjectPane


def _capture_navigation(pane: ProjectPane) -> list[str]:
    captured: list[str] = []
    pane.navigateToPathRequested.connect(captured.append)
    return captured


def test_component_activation_emits_navigation_for_placeholder(tmp_path, qapp):
    pane = ProjectPane()
    try:
        child_dir = tmp_path / "child"
        child_dir.mkdir()
        component = ProjectComponent(
            path=str(child_dir),
            label="Child",
            kind="placeholder",
        )

        pane.set_project(str(tmp_path), label="Root", components=[component])
        captured = _capture_navigation(pane)
        item = pane._components.item(0)
        assert item is not None

        pane._components.itemActivated.emit(item)

        assert captured == [str(child_dir.resolve())]
    finally:
        pane.deleteLater()
        qapp.processEvents()


def test_component_activation_emits_navigation_for_directory_component(tmp_path, qapp):
    pane = ProjectPane()
    try:
        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        component = ProjectComponent(
            path=str(sub_dir),
            label="Sub",
            kind="component",
        )

        pane.set_project(str(tmp_path), label="Root", components=[component])
        captured = _capture_navigation(pane)
        item = pane._components.item(0)
        assert item is not None

        pane._components.itemActivated.emit(item)

        assert captured == [str(sub_dir.resolve())]
    finally:
        pane.deleteLater()
        qapp.processEvents()


def test_component_activation_ignores_file_components(tmp_path, qapp):
    pane = ProjectPane()
    try:
        part_file = tmp_path / "part.3mf"
        part_file.write_text("dummy")
        component = ProjectComponent(
            path=str(part_file),
            label="Part",
            kind="component",
        )

        pane.set_project(str(tmp_path), label="Root", components=[component])
        captured = _capture_navigation(pane)
        item = pane._components.item(0)
        assert item is not None

        pane._components.itemActivated.emit(item)

        assert captured == []
    finally:
        pane.deleteLater()
        qapp.processEvents()


def test_component_activation_resolves_relative_placeholder_paths(tmp_path, qapp):
    pane = ProjectPane()
    try:
        relative_dir = tmp_path / "relative"
        relative_dir.mkdir()
        component = ProjectComponent(
            path="relative",
            label="Relative",
            kind="placeholder",
        )

        pane.set_project(str(tmp_path), label="Root", components=[component])
        captured = _capture_navigation(pane)
        item = pane._components.item(0)
        assert item is not None

        pane._components.itemActivated.emit(item)

        assert captured == [str(relative_dir.resolve())]
    finally:
        pane.deleteLater()
        qapp.processEvents()


def test_component_metadata_attached_to_item(tmp_path, qapp):
    pane = ProjectPane()
    try:
        metadata = {"author": "Ada"}
        component = ProjectComponent(
            path=str(tmp_path / "part.stl"),
            label="Part",
            metadata=metadata,
        )

        pane.set_project(str(tmp_path), label="Root", components=[component])
        item = pane._components.item(0)
        assert item is not None
        stored = item.data(Qt.UserRole + 2)
        assert stored == metadata
    finally:
        pane.deleteLater()
        qapp.processEvents()


def test_component_selection_passes_metadata(tmp_path, qapp, monkeypatch):
    pane = ProjectPane()
    try:
        metadata = {"author": "Ada"}
        component = ProjectComponent(
            path=str(tmp_path / "piece.stl"),
            label="Piece",
            metadata=metadata,
        )
        pane.set_project(str(tmp_path), label="Root", components=[component])
        captured: dict[str, Any] = {}

        def fake_set_item(path, *, label, metadata, asset_record):
            captured["metadata"] = metadata

        monkeypatch.setattr(pane._preview, "set_item", fake_set_item)
        item = pane._components.item(0)
        assert item is not None
        pane._handle_component_selected(item, None)
        assert captured["metadata"] == metadata
    finally:
        pane.deleteLater()
        qapp.processEvents()
