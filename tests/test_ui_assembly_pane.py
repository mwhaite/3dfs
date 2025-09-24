"""Tests exercising the assembly pane navigation helpers."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt

from three_dfs.ui.assembly_pane import AssemblyComponent, AssemblyPane


def _capture_navigation(pane: AssemblyPane) -> list[str]:
    captured: list[str] = []
    pane.navigateToPathRequested.connect(captured.append)
    return captured


def test_component_activation_emits_navigation_for_placeholder(tmp_path, qapp):
    pane = AssemblyPane()
    try:
        child_dir = tmp_path / "child"
        child_dir.mkdir()
        component = AssemblyComponent(
            path=str(child_dir),
            label="Child",
            kind="placeholder",
        )

        pane.set_assembly(str(tmp_path), label="Root", components=[component])
        captured = _capture_navigation(pane)
        item = pane._components.item(0)
        assert item is not None

        pane._components.itemActivated.emit(item)

        assert captured == [str(child_dir.resolve())]
    finally:
        pane.deleteLater()
        qapp.processEvents()


def test_component_activation_emits_navigation_for_directory_component(tmp_path, qapp):
    pane = AssemblyPane()
    try:
        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        component = AssemblyComponent(
            path=str(sub_dir),
            label="Sub",
            kind="component",
        )

        pane.set_assembly(str(tmp_path), label="Root", components=[component])
        captured = _capture_navigation(pane)
        item = pane._components.item(0)
        assert item is not None

        pane._components.itemActivated.emit(item)

        assert captured == [str(sub_dir.resolve())]
    finally:
        pane.deleteLater()
        qapp.processEvents()


def test_component_activation_ignores_file_components(tmp_path, qapp):
    pane = AssemblyPane()
    try:
        part_file = tmp_path / "part.3mf"
        part_file.write_text("dummy")
        component = AssemblyComponent(
            path=str(part_file),
            label="Part",
            kind="component",
        )

        pane.set_assembly(str(tmp_path), label="Root", components=[component])
        captured = _capture_navigation(pane)
        item = pane._components.item(0)
        assert item is not None

        pane._components.itemActivated.emit(item)

        assert captured == []
    finally:
        pane.deleteLater()
        qapp.processEvents()


def test_component_activation_resolves_relative_placeholder_paths(tmp_path, qapp):
    pane = AssemblyPane()
    try:
        relative_dir = tmp_path / "relative"
        relative_dir.mkdir()
        component = AssemblyComponent(
            path="relative",
            label="Relative",
            kind="placeholder",
        )

        pane.set_assembly(str(tmp_path), label="Root", components=[component])
        captured = _capture_navigation(pane)
        item = pane._components.item(0)
        assert item is not None

        pane._components.itemActivated.emit(item)

        assert captured == [str(relative_dir.resolve())]
    finally:
        pane.deleteLater()
        qapp.processEvents()


def test_component_metadata_attached_to_item(tmp_path, qapp):
    pane = AssemblyPane()
    try:
        metadata = {"author": "Ada"}
        component = AssemblyComponent(
            path=str(tmp_path / "part.stl"),
            label="Part",
            metadata=metadata,
        )

        pane.set_assembly(str(tmp_path), label="Root", components=[component])
        item = pane._components.item(0)
        assert item is not None
        stored = item.data(Qt.UserRole + 2)
        assert stored == metadata
    finally:
        pane.deleteLater()
        qapp.processEvents()


def test_component_selection_passes_metadata(tmp_path, qapp, monkeypatch):
    pane = AssemblyPane()
    try:
        metadata = {"author": "Ada"}
        component = AssemblyComponent(
            path=str(tmp_path / "piece.stl"),
            label="Piece",
            metadata=metadata,
        )
        pane.set_assembly(str(tmp_path), label="Root", components=[component])
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
