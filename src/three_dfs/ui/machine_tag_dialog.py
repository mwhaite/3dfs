"""Dialog for managing Machine:<ID> tags on a G-code asset."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


@dataclass(slots=True)
class MachineTagState:
    """Store the available and assigned machine tags for an asset."""

    available: list[str]
    assigned: list[str]


class MachineTagDialog(QDialog):
    """Allow users to manage Machine:<ID> tags for a single asset."""

    def __init__(
        self,
        *,
        parent: QWidget | None = None,
        asset_path: str,
        current_tags: Iterable[str],
        available_tags: Iterable[str],
        tag_manager,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Machine Tags")
        self.resize(420, 320)

        self._asset_path = asset_path
        self._tag_manager = tag_manager
        self._state = MachineTagState(
            available=sorted({tag for tag in available_tags if tag}),
            assigned=sorted({tag for tag in current_tags if tag}),
        )

        layout = QVBoxLayout(self)
        description = QLabel(
            "Manage Machine:<ID> tags for this G-code file." " Tags added or renamed here apply only to this file."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)

        assigned_label = QLabel("Assigned Machine Tags")
        grid.addWidget(assigned_label, 0, 0)
        available_label = QLabel("Available Machine Tags")
        available_label.setAlignment(Qt.AlignLeft)
        grid.addWidget(available_label, 0, 2)

        self._assigned_list = QListWidget()
        self._available_list = QListWidget()
        self._refresh_lists()

        grid.addWidget(self._assigned_list, 1, 0)
        grid.addWidget(self._available_list, 1, 2)

        button_column = QVBoxLayout()
        button_column.setSpacing(6)

        assign_button = QPushButton("Assign →")
        assign_button.clicked.connect(self._assign_selected)
        button_column.addWidget(assign_button)

        remove_button = QPushButton("← Remove")
        remove_button.clicked.connect(self._remove_selected)
        button_column.addWidget(remove_button)

        rename_button = QPushButton("Rename…")
        rename_button.clicked.connect(self._rename_selected)
        button_column.addWidget(rename_button)

        add_button = QPushButton("New Tag…")
        add_button.clicked.connect(self._create_tag)
        button_column.addWidget(add_button)

        button_column.addStretch(1)
        button_container = QWidget()
        button_container.setLayout(button_column)
        grid.addWidget(button_container, 1, 1)

        layout.addLayout(grid)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _refresh_lists(self) -> None:
        self._assigned_list.clear()
        for tag in self._state.assigned:
            self._assigned_list.addItem(QListWidgetItem(tag))

        self._available_list.clear()
        for tag in self._state.available:
            if tag in self._state.assigned:
                continue
            self._available_list.addItem(QListWidgetItem(tag))

    def _create_tag(self) -> None:
        tag_id, ok = QInputDialog.getText(
            self,
            "New Machine Tag",
            "Machine identifier:",
        )
        if not ok:
            return
        normalized = self._normalize_tag(tag_id)
        if not normalized:
            QMessageBox.warning(
                self,
                "Invalid Tag",
                "Machine tags must contain at least one alphanumeric character.",
            )
            return
        if normalized in self._state.assigned:
            QMessageBox.information(
                self,
                "Duplicate Tag",
                "This machine tag is already assigned to the file.",
            )
            return
        self._apply_tag_change(assign=[normalized])

    def _assign_selected(self) -> None:
        selected = [item.text() for item in self._available_list.selectedItems()]
        if not selected:
            return
        self._apply_tag_change(assign=selected)

    def _remove_selected(self) -> None:
        selected = [item.text() for item in self._assigned_list.selectedItems()]
        if not selected:
            return
        self._apply_tag_change(remove=selected)

    def _rename_selected(self) -> None:
        items = self._assigned_list.selectedItems()
        if not items:
            return
        old_tag = items[0].text()
        base_id = old_tag.split(":", 1)[1] if ":" in old_tag else old_tag
        new_id, ok = QInputDialog.getText(
            self,
            "Rename Machine Tag",
            "Machine identifier:",
            text=base_id,
        )
        if not ok:
            return
        normalized = self._normalize_tag(new_id)
        if not normalized:
            QMessageBox.warning(
                self,
                "Invalid Tag",
                "Machine tags must contain at least one alphanumeric character.",
            )
            return
        if normalized == old_tag:
            return
        self._apply_tag_change(rename={old_tag: normalized})

    def _apply_tag_change(
        self,
        *,
        assign: Iterable[str] | None = None,
        remove: Iterable[str] | None = None,
        rename: dict[str, str] | None = None,
    ) -> None:
        assign = list(assign or [])
        remove = list(remove or [])
        rename_map = dict(rename or {})

        try:
            self._tag_manager.update_machine_tags(
                asset_path=self._asset_path,
                assign=assign,
                remove=remove,
                rename=rename_map,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Tag Update Failed", str(exc))
            return

        for tag in assign:
            if tag not in self._state.assigned:
                self._state.assigned.append(tag)
        for tag in remove:
            if tag in self._state.assigned:
                self._state.assigned.remove(tag)
        for old_tag, new_tag in rename_map.items():
            if old_tag in self._state.assigned:
                self._state.assigned.remove(old_tag)
                self._state.assigned.append(new_tag)
        self._state.assigned.sort()

        all_tags = set(self._state.available)
        all_tags.update(assign)
        all_tags.update(rename_map.values())
        all_tags.difference_update(remove)
        self._state.available = sorted(all_tags)
        self._refresh_lists()

    @staticmethod
    def _normalize_tag(raw: str) -> str | None:
        text = (raw or "").strip()
        if not text:
            return None
        core = text.split(":", 1)[-1].strip()
        if not core:
            return None
        return f"Machine:{core}"
