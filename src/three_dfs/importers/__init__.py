"""Importer manager."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .base import Importer
from .myminifactory import MyMiniFactoryImporter
from .thingiverse import ThingiverseImporter

if TYPE_CHECKING:
    from ..application.settings import AppSettings
from ..application.settings import AppSettings


class ImporterManager:
    """Importer manager."""

    def __init__(self):
        self._importers: dict[str, Importer] = {}
        self._register_importers()

    def _register_importers(self):
        """Register all available importers."""
        self._importers["thingiverse"] = ThingiverseImporter()
        self._importers["myminifactory"] = MyMiniFactoryImporter()

    def get_importer(self, name: str) -> Importer | None:
        """Get an importer by name."""
        return self._importers.get(name)

    def import_container(self, name: str, url: str, settings: AppSettings) -> Path:
        """Import a container from a given URL."""
        importer = self.get_importer(name)
        if importer:
            return importer.import_container(url, settings)
        else:
            raise ValueError(f"No importer found for: {name}")
