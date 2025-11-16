"""Tests for Machine:<ID> tag management on G-code assets."""

from __future__ import annotations

from pathlib import Path

import pytest

from three_dfs.importer import GCODE_EXTENSIONS
from three_dfs.storage import AssetRepository, AssetService, SQLiteStorage
from three_dfs.ui.preview_pane import (
    PreviewOutcome,
    PreviewPane,
    _PreviewMachineTagManager,
)


def _make_service(tmp_path: Path) -> AssetService:
    storage = SQLiteStorage(tmp_path / "assets.sqlite3")
    repository = AssetRepository(storage)
    return AssetService(repository)


@pytest.fixture(autouse=True)
def reset_gcode_extensions_sanity() -> None:
    # Ensure test expectations stay aligned with recognised suffixes.
    assert ".gcode" in GCODE_EXTENSIONS
    assert ".stl" not in GCODE_EXTENSIONS


@pytest.mark.usefixtures("qapp")
def test_preview_populates_machine_tag_label(tmp_path: Path, qapp) -> None:
    service = _make_service(tmp_path)

    gcode_path = tmp_path / "sample.gcode"
    gcode_path.write_text("G1 X0 Y0 F1200\n")

    record = service.create_asset(str(gcode_path), label="sample", metadata={})
    service.add_tag(str(gcode_path), "Machine:TestRig")

    pane = PreviewPane(base_path=tmp_path, asset_service=service)
    pane.set_item(str(gcode_path), asset_record=record, entry_kind="file")
    outcome = PreviewOutcome(
        path=gcode_path.resolve(),
        metadata=[],
        asset_record=service.get_asset(record.id),
        text_content=gcode_path.read_text(),
        text_truncated=False,
    )
    pane._apply_outcome(outcome)
    pane.show()
    qapp.processEvents()

    assert pane._machine_tag_container.isVisible()
    label_text = pane._machine_tag_label.text()
    assert label_text.startswith("Machine: <a ")
    assert ">TestRig<" in label_text

    assert pane._tabs.isTabEnabled(pane._thumbnail_tab_index)
    assert pane._tabs.isTabEnabled(pane._text_tab_index)
    assert "G1 X0 Y0" in pane._text_view.toPlainText()

    emitted: list[str] = []
    pane.tagFilterRequested.connect(emitted.append)
    # Simulate activating the first link
    href = label_text.split('href="')[1].split('"')[0]
    pane._handle_machine_tag_link(href)
    assert emitted == ["Machine:TestRig"]


@pytest.mark.usefixtures("qapp")
def test_preview_formats_multiple_machine_tags(tmp_path: Path, qapp) -> None:
    service = _make_service(tmp_path)

    gcode_path = tmp_path / "sample.gcode"
    gcode_path.write_text("G1 X0 Y0 F1200\n")

    record = service.create_asset(str(gcode_path), label="sample", metadata={})
    service.add_tag(str(gcode_path), "Machine:RigA")
    service.add_tag(str(gcode_path), "Machine:RigB")

    pane = PreviewPane(base_path=tmp_path, asset_service=service)
    pane.set_item(str(gcode_path), asset_record=record, entry_kind="file")
    outcome = PreviewOutcome(
        path=gcode_path.resolve(),
        metadata=[],
        asset_record=service.get_asset(record.id),
        text_content=gcode_path.read_text(),
        text_truncated=False,
    )
    pane._apply_outcome(outcome)
    pane.show()
    qapp.processEvents()

    label_text = pane._machine_tag_label.text()
    assert label_text.startswith("Machines: <a ")
    assert label_text.count("<a href=") == 2
    assert ">RigA<" in label_text and ">RigB<" in label_text

    assert pane._tabs.isTabEnabled(pane._thumbnail_tab_index)
    assert pane._tabs.isTabEnabled(pane._text_tab_index)


def test_preview_machine_tag_manager_updates_tags(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    gcode_path = tmp_path / "sample.gcode"
    gcode_path.write_text("G1 X0 Y0 F1200\n")

    service.create_asset(str(gcode_path), label="sample", metadata={})

    manager = _PreviewMachineTagManager(service)
    manager.update_machine_tags(
        asset_path=str(gcode_path),
        assign=["Machine:RigA"],
        remove=[],
        rename={},
    )
    assert "Machine:RigA" in service.tags_for_path(str(gcode_path))

    manager.update_machine_tags(
        asset_path=str(gcode_path),
        assign=[],
        remove=[],
        rename={"Machine:RigA": "Machine:RigB"},
    )
    tags = service.tags_for_path(str(gcode_path))
    assert "Machine:RigB" in tags and "Machine:RigA" not in tags

    manager.update_machine_tags(
        asset_path=str(gcode_path),
        assign=[],
        remove=["Machine:RigB"],
        rename={},
    )
    assert "Machine:RigB" not in service.tags_for_path(str(gcode_path))
