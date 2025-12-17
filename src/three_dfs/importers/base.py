"""Base class for all importers."""

from __future__ import annotations

import abc

from ..application.settings import AppSettings


class Importer(abc.ABC):
    """Base class for all importers."""

    @abc.abstractmethod
    def import_container(self, url: str, settings: AppSettings) -> None:
        """Import a container from a given URL."""
        raise NotImplementedError
