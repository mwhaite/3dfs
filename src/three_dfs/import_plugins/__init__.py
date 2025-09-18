"""Plugin interface and utilities for the asset importer."""

from __future__ import annotations

import logging
import re
from collections.abc import MutableMapping
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

Metadata = MutableMapping[str, Any]
"""Mutable mapping used for metadata returned by import plugins."""

ENTRYPOINT_GROUP = "three_dfs.import_plugins"
"""Entry-point group used to auto-discover importer plugins."""


@runtime_checkable
class ImportPlugin(Protocol):
    """Protocol describing the expected importer plugin behaviour."""

    def can_handle(self, source: str) -> bool:
        """Return ``True`` when the plugin is able to fetch *source*."""

    def fetch(self, source: str, destination: Path) -> Metadata:
        """Download the asset identified by *source* into *destination*."""


_registry: list[ImportPlugin] = []
_entry_points_loaded = False

__all__ = [
    "ENTRYPOINT_GROUP",
    "ImportPlugin",
    "Metadata",
    "iter_plugins",
    "load_entrypoint_plugins",
    "register_plugin",
    "scaffold_plugin",
    "unregister_plugin",
]


def register_plugin(plugin: ImportPlugin) -> ImportPlugin:
    """Register *plugin* with the importer registry."""

    if plugin not in _registry:
        _registry.append(plugin)
    return plugin


def unregister_plugin(plugin: ImportPlugin) -> None:
    """Remove *plugin* from the importer registry when present."""

    try:
        _registry.remove(plugin)
    except ValueError:  # pragma: no cover - defensive guard
        pass


def iter_plugins() -> tuple[ImportPlugin, ...]:
    """Return the registered plugins, loading entry points on first access."""

    _ensure_entry_points_loaded()
    return tuple(_registry)


def load_entrypoint_plugins(group: str = ENTRYPOINT_GROUP) -> tuple[ImportPlugin, ...]:
    """Discover and register plugins exposed through *group* entry points."""

    global _entry_points_loaded
    discovered = tuple(_discover_entrypoint_plugins(group))
    for plugin in discovered:
        register_plugin(plugin)
    if group == ENTRYPOINT_GROUP:
        _entry_points_loaded = True
    return discovered


def scaffold_plugin(repo_name: str, target_dir: str | Path) -> Path:
    """Create a skeleton plugin for *repo_name* inside *target_dir*."""

    target_path = Path(target_dir).expanduser()
    target_path.mkdir(parents=True, exist_ok=True)

    module_stub = _normalize_module_name(repo_name)
    class_name = _build_class_name(repo_name)

    filename = f"{module_stub}_plugin.py"
    destination = target_path / filename
    if destination.exists():
        raise FileExistsError(f"Plugin module {destination} already exists")

    template = f'''"""Import plugin scaffold for {repo_name}."""

from __future__ import annotations

from pathlib import Path

from three_dfs.import_plugins import ImportPlugin, Metadata, register_plugin


class {class_name}(ImportPlugin):
    """Interact with {repo_name} to import 3D assets."""

    def can_handle(self, source: str) -> bool:
        """Return ``True`` when *source* belongs to {repo_name}."""
        # TODO: Refine detection logic for {repo_name} identifiers.
        return source.startswith("{module_stub}://")

    def fetch(self, source: str, destination: Path) -> Metadata:
        """Download the asset identified by *source* into *destination*."""
        # TODO: Handle authentication for {repo_name} APIs.
        # TODO: Scrape or download the asset payload into *destination*.
        # TODO: Map remote metadata fields into the returned dictionary.
        raise NotImplementedError(
            "Fetching assets from {repo_name} is not implemented yet."
        )


register_plugin({class_name}())
'''

    destination.write_text(template, encoding="utf-8")
    return destination


def _ensure_entry_points_loaded() -> None:
    """Load entry-point plugins once on demand."""

    global _entry_points_loaded
    if _entry_points_loaded:
        return
    load_entrypoint_plugins()


def _discover_entrypoint_plugins(group: str) -> list[ImportPlugin]:
    """Return plugins discovered for *group* entry points."""

    entries = _select_entry_points(group)
    plugins: list[ImportPlugin] = []

    for entry in entries:
        try:
            loaded = entry.load()
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed to load import plugin entry point %s", entry.name)
            continue

        plugin = _coerce_plugin(loaded)
        if plugin is None:
            logger.warning(
                "Entry point %s from %s did not provide an ImportPlugin",
                entry.name,
                entry.module,
            )
            continue

        plugins.append(plugin)

    return plugins


def _select_entry_points(group: str) -> tuple[EntryPoint, ...]:
    """Return entry points for *group* with compatibility fallbacks."""

    discovered = entry_points()
    if isinstance(discovered, dict):  # pragma: no cover - legacy interface
        entries = discovered.get(group, ())
    else:
        entries = discovered.select(group=group)
    return tuple(entries)


def _coerce_plugin(candidate: Any) -> ImportPlugin | None:
    """Convert entry-point *candidate* to an :class:`ImportPlugin` instance."""

    if isinstance(candidate, ImportPlugin):
        return candidate

    if callable(candidate):
        try:
            plugin = candidate()
        except TypeError:
            logger.exception(
                "Import plugin factory %r could not be instantiated", candidate
            )
            return None

        if isinstance(plugin, ImportPlugin):
            return plugin

    return None


def _normalize_module_name(name: str) -> str:
    """Return a filesystem-friendly module stub for *name*."""

    module = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    return module or "import_plugin"


def _build_class_name(name: str) -> str:
    """Return a CamelCase class name suitable for *name*."""

    parts = re.split(r"[^0-9a-zA-Z]+", name)
    class_base = "".join(part.capitalize() for part in parts if part)
    if not class_base:
        class_base = "External"
    if not class_base.endswith("Plugin"):
        class_base = f"{class_base}ImportPlugin"
    return class_base
