"""Enhanced Thingiverse API importer using the WebAPI base class."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .api_base import WebAPIImporter


class ThingiverseAPI(WebAPIImporter):
    """Enhanced API client for Thingiverse."""

    def __init__(self, token: str | None = None):
        token = token or os.environ.get("THINGIVERSE_TOKEN")
        super().__init__("https://api.thingiverse.com/", token)
        self.session.headers.update(
            {
                "Accept": "application/json",
            }
        )

    def get_thing(self, thing_id: str) -> dict[str, Any]:
        """Get details about a specific thing."""
        return self._get(f"things/{thing_id}")

    def get_thing_files(self, thing_id: str) -> list[dict[str, Any]]:
        """Get files for a specific thing."""
        response = self._get(f"things/{thing_id}/files")
        return response.get("items", response) if isinstance(response, dict) else response

    def download_file(self, download_url: str, destination: Path) -> None:
        """Download a file from Thingiverse."""
        self._download_file(download_url, destination)

    def extract_thing_id(self, source: str) -> str | None:
        """Extract thing ID from various URL formats."""
        patterns = [r"thing:(\d+)", r"/things?/(\d+)", r"thingiverse\.com/[^/]+/(\d+)"]
        for pattern in patterns:
            match = re.search(pattern, source, re.IGNORECASE)
            if match:
                return match.group(1)

        # If it's just a number
        if source.isdigit():
            return source

        return None
