"""Structured schema helpers for container-level metadata."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse

__all__ = [
    "PrintedStatus",
    "PriorityLevel",
    "ContactEntry",
    "ExternalLink",
    "ContainerMetadata",
    "parse_container_metadata",
]


class PrintedStatus(str, Enum):
    """Lifecycle state describing whether the container has been printed."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    PRINTED = "printed"
    DEPRECATED = "deprecated"


class PriorityLevel(str, Enum):
    """Scheduling priority that helps triage containers."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


@dataclass(slots=True)
class ContactEntry:
    """Describe a primary contact or owner for a container."""

    name: str
    role: str | None = None
    email: str | None = None
    url: str | None = None
    notes: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> ContactEntry | None:
        name = _clean_text(payload.get("name"))
        if not name:
            return None
        role = _clean_text(payload.get("role"))
        email = _clean_text(payload.get("email"))
        url = _clean_url(payload.get("url"))
        notes = _clean_text(payload.get("notes"))
        return cls(name=name, role=role, email=email, url=url, notes=notes)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name}
        if self.role:
            data["role"] = self.role
        if self.email:
            data["email"] = self.email
        if self.url:
            data["url"] = self.url
        if self.notes:
            data["notes"] = self.notes
        return data


@dataclass(slots=True)
class ExternalLink:
    """Link to related resources such as documentation or project pages."""

    label: str
    url: str
    kind: str | None = None
    description: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> ExternalLink | None:
        label = _clean_text(payload.get("label"))
        url = _clean_url(payload.get("url"))
        if not label or not url:
            return None
        kind = _clean_text(payload.get("kind"))
        description = _clean_text(payload.get("description"))
        return cls(label=label, url=url, kind=kind, description=description)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"label": self.label, "url": self.url}
        if self.kind:
            data["kind"] = self.kind
        if self.description:
            data["description"] = self.description
        return data


@dataclass(slots=True)
class ContainerMetadata:
    """Structured representation of container-level metadata."""

    due_date: date | None = None
    printed_status: PrintedStatus = PrintedStatus.NOT_STARTED
    priority: PriorityLevel = PriorityLevel.NORMAL
    notes: str | None = None
    contacts: list[ContactEntry] = field(default_factory=list)
    external_links: list[ExternalLink] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> ContainerMetadata:
        payload = payload or {}
        due_date = _parse_date(payload.get("due_date"))
        printed_status = _parse_enum(payload.get("printed_status"), PrintedStatus, PrintedStatus.NOT_STARTED)
        priority = _parse_enum(payload.get("priority"), PriorityLevel, PriorityLevel.NORMAL)
        notes = _clean_text(payload.get("notes"))
        contacts = _parse_contacts(payload.get("contacts"))
        external_links = _parse_links(payload.get("external_links"))
        return cls(
            due_date=due_date,
            printed_status=printed_status,
            priority=priority,
            notes=notes,
            contacts=contacts,
            external_links=external_links,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "printed_status": self.printed_status.value,
            "priority": self.priority.value,
            "contacts": [contact.to_dict() for contact in self.contacts],
            "external_links": [link.to_dict() for link in self.external_links],
        }
        if self.due_date is not None:
            data["due_date"] = self.due_date.isoformat()
        if self.notes:
            data["notes"] = self.notes
        return data

    def update(self, **kwargs: Any) -> ContainerMetadata:
        """Return a new metadata instance with selected fields replaced."""

        current = self.to_dict()
        current.update(kwargs)
        return ContainerMetadata.from_mapping(current)


def parse_container_metadata(payload: Mapping[str, Any] | None) -> ContainerMetadata:
    """Return a :class:`ContainerMetadata` instance for *payload*."""

    return ContainerMetadata.from_mapping(payload)


def _clean_text(value: Any) -> str | None:
    if isinstance(value, str):
        candidate = value.strip()
        return candidate or None
    return None


def _clean_url(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme and (parsed.netloc or parsed.scheme == "mailto"):
        return text
    return None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            return None
    return None


def _parse_enum(value: Any, enum_cls: type[Enum], default: Enum) -> Enum:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        for member in enum_cls:  # type: ignore[arg-type]
            if member.value == lowered:
                return member
    return default


def _parse_contacts(value: Any) -> list[ContactEntry]:
    entries: list[ContactEntry] = []
    if isinstance(value, Mapping):
        maybe_entry = ContactEntry.from_mapping(value)
        if maybe_entry:
            entries.append(maybe_entry)
        return entries
    if isinstance(value, Iterable):
        for item in value:
            if isinstance(item, Mapping):
                maybe_entry = ContactEntry.from_mapping(item)
                if maybe_entry:
                    entries.append(maybe_entry)
    return entries


def _parse_links(value: Any) -> list[ExternalLink]:
    entries: list[ExternalLink] = []
    if isinstance(value, Mapping):
        maybe_entry = ExternalLink.from_mapping(value)
        if maybe_entry:
            entries.append(maybe_entry)
        return entries
    if isinstance(value, Iterable):
        for item in value:
            if isinstance(item, Mapping):
                maybe_entry = ExternalLink.from_mapping(item)
                if maybe_entry:
                    entries.append(maybe_entry)
    return entries
