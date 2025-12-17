from three_dfs.container_metadata import ContactEntry, ExternalLink
from three_dfs.ui.container_metadata_dialog import (
    _format_contacts,
    _format_links,
    parse_contacts_block,
    parse_links_block,
)


def test_parse_contacts_block_round_trip() -> None:
    contacts_text = "Ada Lovelace | Designer | ada@example.com | https://example.com/ada | Primary contact\n"
    contacts_text += "Bruce Wayne | | bruce@example.com | | "
    entries, errors = parse_contacts_block(contacts_text)
    assert errors == []
    assert len(entries) == 2
    assert entries[0].name == "Ada Lovelace"
    assert entries[0].url == "https://example.com/ada"
    assert entries[1].role is None

    restored = _format_contacts(entries)
    assert "Ada Lovelace" in restored
    assert "Bruce Wayne" in restored


def test_parse_links_block_round_trip() -> None:
    links_text = "Docs | https://example.com/docs | guide | Main reference\n"
    links_text += "Source | https://example.com | | "
    entries, errors = parse_links_block(links_text)
    assert errors == []
    assert len(entries) == 2
    assert entries[0].label == "Docs"
    assert entries[0].kind == "guide"
    assert entries[1].description is None

    restored = _format_links(entries)
    assert "Docs" in restored
    assert "Source" in restored


def test_parse_blocks_capture_errors() -> None:
    contacts, contact_errors = parse_contacts_block(" | Missing name\nValid | role | | |")
    assert len(contacts) == 1
    assert contact_errors == ["Contact line 1: name is required."]

    links, link_errors = parse_links_block("Docs | \n | https://example.com")
    assert links == []
    assert link_errors == [
        "Link line 1: label and URL are required.",
        "Link line 2: label and URL are required.",
    ]
