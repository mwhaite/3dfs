"""Tests covering the Thingiverse import plugin."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from three_dfs.import_plugins.thingiverse_plugin import ThingiverseImportPlugin


class FakeResponse:
    """File-like response object used to emulate urllib downloads."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._position = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._payload) - self._position
        start = self._position
        end = min(start + size, len(self._payload))
        self._position = end
        return self._payload[start:end]

    def close(self) -> None:  # pragma: no cover - compatibility stub
        self._payload = b""

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class FakeOpener:
    """Callable mimicking :func:`urllib.request.urlopen` for tests."""

    def __init__(self, responses: dict[str, bytes]) -> None:
        self._responses = responses
        self.requests: list[Any] = []

    def __call__(self, request: Any) -> FakeResponse:
        url = getattr(request, "full_url", request)
        self.requests.append(request)
        try:
            payload = self._responses[url]
        except KeyError as err:  # pragma: no cover - defensive guard
            raise AssertionError(f"Unexpected URL requested: {url}") from err
        return FakeResponse(payload)


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("https://www.thingiverse.com/thing:12345", True),
        ("https://www.thingiverse.com/things/67890", True),
        ("thing:555", True),
        ("555", True),
        ("https://example.com/thing:555", False),
        ("", False),
    ],
)
def test_can_handle_variants(identifier: str, expected: bool) -> None:
    """Plugin should recognise multiple Thingiverse identifier formats."""

    plugin = ThingiverseImportPlugin(token="dummy", opener=FakeOpener({}))
    assert plugin.can_handle(identifier) is expected


def test_fetch_downloads_primary_file(tmp_path: Path) -> None:
    """Fetching a Thingiverse listing should download the preferred asset."""

    thing_payload = {
        "id": 4242,
        "name": "Calibration Cube",
        "description": "Simple cube used for calibration.",
        "thumbnail": "https://cdn.thingiverse.com/thing.jpg",
        "creator": {
            "id": 101,
            "name": "makerbot",
            "public_url": "https://www.thingiverse.com/makerbot",
            "url": "https://api.thingiverse.com/users/makerbot",
        },
        "tags": [{"name": "calibration"}, {"name": "cube"}],
    }
    files_payload = [
        {
            "id": 1,
            "name": "calibration_cube.stl",
            "size": 1234,
            "public_url": "https://www.thingiverse.com/thing:4242/files",
            "download_url": "https://cdn.thingiverse.com/download/calibration_cube.stl",
            "is_primary": True,
        },
        {
            "id": 2,
            "name": "notes.txt",
            "size": 56,
            "public_url": "https://www.thingiverse.com/thing:4242/files",
            "download_url": "https://cdn.thingiverse.com/download/notes.txt",
        },
    ]
    downloaded_asset = b"solid cube\nendsolid cube\n"

    api_root = ThingiverseImportPlugin.API_ROOT
    responses = {
        f"{api_root}/things/4242": json.dumps(thing_payload).encode("utf-8"),
        f"{api_root}/things/4242/files": json.dumps(files_payload).encode("utf-8"),
        "https://cdn.thingiverse.com/download/calibration_cube.stl": downloaded_asset,
    }
    opener = FakeOpener(responses)
    plugin = ThingiverseImportPlugin(token="secret-token", opener=opener)

    destination = tmp_path / "download.tmp"
    metadata = plugin.fetch("https://www.thingiverse.com/thing:4242", destination)

    assert destination.read_bytes() == downloaded_asset
    assert metadata["filename"] == "calibration_cube.stl"
    assert metadata["extension"] == ".stl"
    assert metadata["label"] == "Calibration Cube"

    thing_metadata = metadata["thingiverse"]
    assert thing_metadata["id"] == 4242
    assert thing_metadata["creator"]["name"] == "makerbot"
    assert thing_metadata["files"][0]["primary"] is True
    assert thing_metadata["files"][0]["download_url"].endswith("calibration_cube.stl")

    # All HTTP calls should carry the Thingiverse API token.
    auth_headers = [request.headers.get("Authorization") for request in opener.requests]
    assert auth_headers[:2] == ["Bearer secret-token", "Bearer secret-token"]
    assert auth_headers[-1] == "Bearer secret-token"


def test_fetch_requires_api_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plugin should emit a clear error when authentication is missing."""

    monkeypatch.delenv("THINGIVERSE_TOKEN", raising=False)
    plugin = ThingiverseImportPlugin(opener=FakeOpener({}))

    with pytest.raises(RuntimeError, match="Thingiverse API token"):
        plugin.fetch("thing:999", tmp_path / "asset.tmp")


def test_fetch_errors_when_no_supported_files(tmp_path: Path) -> None:
    """Listings without supported file types should raise an error."""

    thing_payload = {"id": 123, "name": "Unsupported"}
    files_payload = [
        {
            "id": 11,
            "name": "notes.txt",
            "download_url": "https://cdn.thingiverse.com/download/notes.txt",
        }
    ]

    api_root = ThingiverseImportPlugin.API_ROOT
    responses = {
        f"{api_root}/things/123": json.dumps(thing_payload).encode("utf-8"),
        f"{api_root}/things/123/files": json.dumps(files_payload).encode("utf-8"),
    }
    plugin = ThingiverseImportPlugin(token="token", opener=FakeOpener(responses))

    with pytest.raises(RuntimeError, match="supported extensions"):
        plugin.fetch("thing:123", tmp_path / "asset.tmp")
