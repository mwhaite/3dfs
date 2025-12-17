from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from ..container_metadata import (
    ContainerMetadata,
    ContactEntry,
    ExternalLink,
    PrintedStatus,
    PriorityLevel,
)

_CONTACT_PLACEHOLDER = "Name | Role | Email | URL | Notes"
_LINK_PLACEHOLDER = "Label | URL | Kind | Description"


@dataclass(slots=True)
class _DialogData:
    due_date: str | None
    printed_status: PrintedStatus
    priority: PriorityLevel
    notes: str | None
    contacts_text: str
    links_text: str


class ContainerMetadataDialog(QDialog):
    """Dialog for editing container-level metadata."""

    def __init__(self, metadata: ContainerMetadata | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Container Metadata")
        self.resize(520, 540)
        self._metadata = metadata or ContainerMetadata()
        self._result_metadata = self._metadata
        self._build_ui()
        self._load_metadata()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._due_date_edit = QLineEdit(self)
        self._due_date_edit.setPlaceholderText("YYYY-MM-DD")
        form.addRow("Due date", self._due_date_edit)

        self._printed_combo = QComboBox(self)
        for status in PrintedStatus:
            self._printed_combo.addItem(status.value.replace("_", " ").title(), status)
        form.addRow("Printed status", self._printed_combo)

        self._priority_combo = QComboBox(self)
        for level in PriorityLevel:
            self._priority_combo.addItem(level.value.title(), level)
        form.addRow("Priority", self._priority_combo)

        self._notes_edit = QPlainTextEdit(self)
        self._notes_edit.setPlaceholderText("Operator notes, handling instructions, etc.")
        form.addRow("Notes", self._notes_edit)

        layout.addLayout(form)

        self._contacts_edit = QPlainTextEdit(self)
        self._contacts_edit.setPlaceholderText(_CONTACT_PLACEHOLDER)
        contacts_label = QLabel("Contacts (one per line):", self)
        layout.addWidget(contacts_label)
        layout.addWidget(self._contacts_edit)

        self._links_edit = QPlainTextEdit(self)
        self._links_edit.setPlaceholderText(_LINK_PLACEHOLDER)
        links_label = QLabel("External links (one per line):", self)
        layout.addWidget(links_label)
        layout.addWidget(self._links_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, Qt.Horizontal, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_metadata(self) -> None:
        data = _DialogData(
            due_date=self._metadata.due_date.isoformat() if self._metadata.due_date else "",
            printed_status=self._metadata.printed_status,
            priority=self._metadata.priority,
            notes=self._metadata.notes or "",
            contacts_text=_format_contacts(self._metadata.contacts),
            links_text=_format_links(self._metadata.external_links),
        )
        self._due_date_edit.setText(data.due_date or "")
        index = self._printed_combo.findData(data.printed_status)
        if index >= 0:
            self._printed_combo.setCurrentIndex(index)
        index = self._priority_combo.findData(data.priority)
        if index >= 0:
            self._priority_combo.setCurrentIndex(index)
        self._notes_edit.setPlainText(data.notes or "")
        self._contacts_edit.setPlainText(data.contacts_text)
        self._links_edit.setPlainText(data.links_text)

    def accept(self) -> None:  # type: ignore[override]
        metadata, error = self._build_metadata()
        if error:
            QMessageBox.warning(self, "Invalid metadata", error)
            return
        if metadata is None:
            metadata = ContainerMetadata()
        self._result_metadata = metadata
        super().accept()

    def result_metadata(self) -> ContainerMetadata:
        return self._result_metadata

    def _build_metadata(self) -> tuple[ContainerMetadata | None, str | None]:
        due_text = self._due_date_edit.text().strip()
        due_value = _parse_iso_date(due_text)
        if due_text and due_value is None:
            return None, "Due date must be in YYYY-MM-DD format."
        printed_status = self._printed_combo.currentData()
        if not isinstance(printed_status, PrintedStatus):
            printed_status = PrintedStatus.NOT_STARTED
        priority = self._priority_combo.currentData()
        if not isinstance(priority, PriorityLevel):
            priority = PriorityLevel.NORMAL

        notes_text = self._notes_edit.toPlainText().strip()
        contacts, contact_errors = parse_contacts_block(self._contacts_edit.toPlainText())
        links, link_errors = parse_links_block(self._links_edit.toPlainText())
        errors = contact_errors + link_errors
        if errors:
            return None, "\n".join(errors)

        return ContainerMetadata(
            due_date=due_value,
            printed_status=printed_status,
            priority=priority,
            notes=notes_text or None,
            contacts=contacts,
            external_links=links,
        ), None


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_contacts_block(value: str) -> tuple[list[ContactEntry], list[str]]:
    entries: list[ContactEntry] = []
    errors: list[str] = []
    for index, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() or None for part in line.split("|")]
        parts += [None] * (5 - len(parts))
        name, role, email, url, notes = parts[:5]
        if not name:
            errors.append(f"Contact line {index}: name is required.")
            continue
        entries.append(ContactEntry(name=name, role=role, email=email, url=url, notes=notes))
    return entries, errors


def parse_links_block(value: str) -> tuple[list[ExternalLink], list[str]]:
    entries: list[ExternalLink] = []
    errors: list[str] = []
    for index, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() or None for part in line.split("|")]
        parts += [None] * (4 - len(parts))
        label, url, kind, description = parts[:4]
        if not label or not url:
            errors.append(f"Link line {index}: label and URL are required.")
            continue
        entries.append(ExternalLink(label=label, url=url, kind=kind, description=description))
    return entries, errors


def _format_contacts(entries: Iterable[ContactEntry]) -> str:
    lines: list[str] = []
    for entry in entries:
        bits = [
            entry.name or "",
            entry.role or "",
            entry.email or "",
            entry.url or "",
            entry.notes or "",
        ]
        lines.append(" | ".join(bits).strip())
    return "\n".join(lines)


def _format_links(entries: Iterable[ExternalLink]) -> str:
    lines: list[str] = []
    for entry in entries:
        bits = [
            entry.label or "",
            entry.url or "",
            entry.kind or "",
            entry.description or "",
        ]
        lines.append(" | ".join(bits).strip())
    return "\n".join(lines)
