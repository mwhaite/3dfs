from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)


@dataclass(frozen=True, slots=True)
class LinkedContainerOption:
    """Describe a linked container and its available components."""

    container_id: int
    label: str
    path: str
    components: Sequence[Mapping[str, Any]]


class LinkedImportDialog(QDialog):
    """Tree view dialog for choosing a component from linked containers."""

    def __init__(
        self,
        parent,
        containers: Sequence[LinkedContainerOption],
    ) -> None:
        super().__init__(parent)
        self._containers = containers
        self._selection: tuple[int, Mapping[str, Any]] | None = None
        self._tree: QTreeWidget | None = None
        self._ok_button = None
        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        self.setWindowTitle("Import From Linked Container")
        layout = QVBoxLayout(self)
        intro = QLabel("Select a linked container component to reference in the current container.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._tree = QTreeWidget(self)
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Component", "Container", "Location"])
        self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self._tree.setSelectionMode(QTreeWidget.SingleSelection)
        self._tree.itemSelectionChanged.connect(self._handle_selection_changed)
        self._tree.itemDoubleClicked.connect(self._handle_item_double_clicked)
        layout.addWidget(self._tree, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_button = buttons.button(QDialogButtonBox.Ok)
        if self._ok_button is not None:
            self._ok_button.setEnabled(False)
        layout.addWidget(buttons)

    def _populate(self) -> None:
        if self._tree is None:
            return
        self._tree.clear()
        for option in self._containers:
            container_item = QTreeWidgetItem([option.label, option.label, option.path])
            container_item.setFlags(Qt.ItemIsEnabled)
            self._tree.addTopLevelItem(container_item)
            for component in option.components:
                child = self._build_component_item(option, component)
                if child is not None:
                    container_item.addChild(child)
            container_item.setExpanded(True)

    def _build_component_item(
        self,
        option: LinkedContainerOption,
        component: Mapping[str, Any],
    ) -> QTreeWidgetItem | None:
        path_value = component.get("path")
        try:
            path_text = str(path_value or "").strip()
        except Exception:
            return None
        if not path_text:
            return None
        label_value = component.get("label")
        try:
            label_text = str(label_value or "").strip()
        except Exception:
            label_text = ""
        if not label_text:
            try:
                label_text = Path(path_text).name
            except Exception:
                label_text = path_text
        location_text = component.get("relative_path")
        if isinstance(location_text, str) and location_text.strip():
            rel_text = location_text.strip()
        else:
            rel_text = path_text

        item = QTreeWidgetItem([label_text, option.label, rel_text])
        item.setData(0, Qt.UserRole, (option.container_id, component))
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        return item

    def _handle_selection_changed(self) -> None:
        if self._tree is None:
            return
        selected = self._tree.selectedItems()
        payload = None
        if selected:
            payload = selected[0].data(0, Qt.UserRole)
        if (
            isinstance(payload, tuple)
            and len(payload) == 2
            and isinstance(payload[0], int)
            and isinstance(payload[1], Mapping)
        ):
            self._selection = (payload[0], payload[1])
            if self._ok_button is not None:
                self._ok_button.setEnabled(True)
        else:
            self._selection = None
            if self._ok_button is not None:
                self._ok_button.setEnabled(False)

    def _handle_item_double_clicked(self, item: QTreeWidgetItem) -> None:
        payload = item.data(0, Qt.UserRole)
        if (
            isinstance(payload, tuple)
            and len(payload) == 2
            and isinstance(payload[0], int)
            and isinstance(payload[1], Mapping)
        ):
            self._selection = (payload[0], payload[1])
            self.accept()

    def accept(self) -> None:  # type: ignore[override]
        if self._selection is None:
            return
        super().accept()

    def selection(self) -> tuple[int, Mapping[str, Any]] | None:
        """Return the selected container/component pair."""

        return self._selection
