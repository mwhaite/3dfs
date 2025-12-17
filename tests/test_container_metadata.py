from datetime import date

from three_dfs.container_metadata import (
    ContainerMetadata,
    ContactEntry,
    ExternalLink,
    PrintedStatus,
    PriorityLevel,
    parse_container_metadata,
)


def test_parse_container_metadata_defaults() -> None:
    meta = parse_container_metadata(None)
    assert meta.due_date is None
    assert meta.printed_status is PrintedStatus.NOT_STARTED
    assert meta.priority is PriorityLevel.NORMAL
    assert meta.contacts == []
    assert meta.external_links == []


def test_parse_container_metadata_with_values() -> None:
    meta = parse_container_metadata(
        {
            "due_date": "2024-05-01",
            "printed_status": "printed",
            "priority": "urgent",
            "notes": "Handle with care",
            "contacts": [
                {"name": "Ada Lovelace", "role": "Designer", "email": "ada@example.com"},
                {"name": "Charles Babbage", "url": "https://example.com/profile"},
            ],
            "external_links": [
                {"label": "Doc", "url": "https://example.com/doc"},
                {"label": "Chat", "url": "mailto:lab@example.com", "kind": "support"},
            ],
        }
    )
    assert meta.due_date == date(2024, 5, 1)
    assert meta.printed_status is PrintedStatus.PRINTED
    assert meta.priority is PriorityLevel.URGENT
    assert meta.notes == "Handle with care"
    assert len(meta.contacts) == 2
    assert isinstance(meta.contacts[0], ContactEntry)
    assert isinstance(meta.external_links[1], ExternalLink)


def test_container_metadata_to_dict_roundtrip() -> None:
    meta = ContainerMetadata(
        due_date=date(2024, 1, 2),
        printed_status=PrintedStatus.IN_PROGRESS,
        priority=PriorityLevel.HIGH,
        notes="Keep dry",
        contacts=[ContactEntry(name="Grace Hopper", email="grace@example.com")],
        external_links=[ExternalLink(label="Source", url="https://example.com")],
    )
    payload = meta.to_dict()
    restored = ContainerMetadata.from_mapping(payload)
    assert restored.due_date == meta.due_date
    assert restored.printed_status == meta.printed_status
    assert restored.priority == meta.priority
    assert restored.contacts[0].name == "Grace Hopper"
    assert restored.external_links[0].label == "Source"


def test_invalid_entries_are_dropped() -> None:
    meta = parse_container_metadata(
        {
            "contacts": [
                {"name": "valid"},
                {"role": "missing name"},
                "not a mapping",
            ],
            "external_links": [
                {"label": "Docs", "url": "https://example.com"},
                {"label": "Broken"},
                42,
            ],
            "due_date": "invalid-date",
            "printed_status": "unknown",
            "priority": "other",
        }
    )
    assert len(meta.contacts) == 1
    assert meta.contacts[0].name == "valid"
    assert len(meta.external_links) == 1
    assert meta.external_links[0].url == "https://example.com"
    assert meta.due_date is None
    assert meta.printed_status is PrintedStatus.NOT_STARTED
    assert meta.priority is PriorityLevel.NORMAL
