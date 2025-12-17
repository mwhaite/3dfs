"""Base classes for web API importers."""

from __future__ import annotations

import abc
import logging
import urllib.parse
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


class WebAPIImporter(abc.ABC):
    """Base class for web API importers."""

    def __init__(self, base_url: str, token: str | None = None):
        self.base_url = base_url
        self.token = token
        self.session = requests.Session()
        if self.token:
            self.session.headers.update({"Authorization": f"Bearer {self.token}"})
        self.session.headers.update({"User-Agent": "3DFS Web Importer"})

    @abc.abstractmethod
    def import_container(self, url: str) -> Path:
        """Import a container from the given URL."""
        pass

    def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make an API request to the service."""
        url = urllib.parse.urljoin(self.base_url, endpoint)
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def _get(self, endpoint: str, **kwargs) -> dict[str, Any]:
        """Make a GET request and return JSON response."""
        response = self._make_request("GET", endpoint, **kwargs)
        return response.json()

    def _download_file(self, url: str, destination: Path) -> None:
        """Download a file from the given URL to the destination."""
        response = self.session.get(url, stream=True)
        response.raise_for_status()

        with open(destination, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

    def _extract_id_from_url(self, url: str, patterns: list[str]) -> str | None:
        """Extract ID from URL using provided patterns."""
        import re

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None


class WebRepositoryImporter(abc.ABC):
    """Base class for web repository importers that use the import plugin system."""

    @abc.abstractmethod
    def can_handle(self, source: str) -> bool:
        """Return True if this importer can handle the given source."""
        pass

    @abc.abstractmethod
    def fetch(self, source: str, destination: Path) -> dict[str, Any]:
        """Fetch the source and save to destination, returning metadata."""
        pass
