"""Plugin helpers for extending remote asset imports."""

from __future__ import annotations

import logging
import re
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "ENTRY_POINT_GROUP",
    "ImportPlugin",
    "Metadata",
    "clear_plugins",
    "discover_plugins",
    "get_plugin_for",
    "iter_plugins",
    "register_plugin",
    "scaffold_plugin",
]

logger = logging.getLogger(__name__)

Metadata = dict[str, Any]
"""Type alias describing metadata dictionaries returned by plugins."""

ENTRY_POINT_GROUP = "three_dfs.import_plugins"
"""Entry point group used to discover third-party import plugins."""


@runtime_checkable
class ImportPlugin(Protocol):
    """Protocol implemented by importer plugins."""

    def can_handle(self, source: str) -> bool:
        """Return ``True`` when this plugin can handle *source*."""

    def fetch(self, source: str, destination: Path) -> Metadata:
        """Download *source* into *destination* and return metadata."""


_PLUGIN_REGISTRY: list[ImportPlugin] = []
_ENTRY_POINTS_LOADED = False


def register_plugin(plugin: ImportPlugin) -> ImportPlugin:
    """Register *plugin* so it can participate in asset imports."""

    if not isinstance(plugin, ImportPlugin):  # pragma: no cover - defensive branch
        message = (
            "Import plugins must implement the ImportPlugin protocol; "
            f"received {type(plugin)!r}"
        )
        raise TypeError(message)

    if not any(existing is plugin for existing in _PLUGIN_REGISTRY):
        _PLUGIN_REGISTRY.append(plugin)
        logger.debug("Registered import plugin %s", plugin)

    return plugin


def clear_plugins() -> None:
    """Remove all registered plugins and reset discovery state."""

    _PLUGIN_REGISTRY.clear()
    global _ENTRY_POINTS_LOADED
    _ENTRY_POINTS_LOADED = False


def discover_plugins(force: bool = False) -> None:
    """Discover plugins exposed via :mod:`importlib.metadata` entry points."""

    global _ENTRY_POINTS_LOADED
    if _ENTRY_POINTS_LOADED and not force:
        return

    if force:
        clear_plugins()

    try:
        entry_points = metadata.entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:  # pragma: no cover - fallback for legacy importlib
        entry_points = metadata.entry_points().get(ENTRY_POINT_GROUP, [])  # type: ignore[index]

    for entry_point in entry_points:
        try:
            plugin = entry_point.load()
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed to load import plugin %s", entry_point.name)
            continue

        try:
            register_plugin(plugin)
        except TypeError:  # pragma: no cover - defensive logging
            logger.exception(
                "Entry point %s returned an incompatible plugin: %r",
                entry_point.name,
                plugin,
            )

    _ENTRY_POINTS_LOADED = True


def iter_plugins() -> tuple[ImportPlugin, ...]:
    """Return the currently registered plugin instances."""

    discover_plugins()
    return tuple(_PLUGIN_REGISTRY)


def get_plugin_for(source: str) -> ImportPlugin | None:
    """Return the first plugin capable of handling *source* if available."""

    for plugin in iter_plugins():
        try:
            if plugin.can_handle(source):
                return plugin
        except Exception:  # pragma: no cover - defensive logging
            logger.exception(
                "Import plugin %s errored during capability check for %s",
                plugin,
                source,
            )
    return None


def scaffold_plugin(repo_name: str, target_dir: Path | str) -> Path:
    """Generate a boilerplate import plugin for *repo_name* in *target_dir*."""

    target_path = Path(target_dir)
    target_path.mkdir(parents=True, exist_ok=True)

    slug = _slugify(repo_name)
    class_name = _camelize(repo_name or "remote") + "Plugin"
    filename = f"{slug}_plugin.py"
    destination = target_path / filename

    if destination.exists():
        raise FileExistsError(f"Plugin scaffold already exists: {destination!s}")

    template = _PLUGIN_TEMPLATE.format(
        repo_name=repo_name,
        slug=slug,
        class_name=class_name,
    )
    destination.write_text(template, encoding="utf-8")
    return destination


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    """Return a filesystem-safe slug based on *value*."""

    slug = _SLUG_RE.sub("_", value.lower()).strip("_")
    return slug or "remote"


def _camelize(value: str) -> str:
    """Return a CamelCase variant of *value*."""

    parts = re.split(r"[^a-zA-Z0-9]+", value)
    filtered = [part for part in parts if part]
    return "".join(part.capitalize() for part in filtered) or "Remote"


_PLUGIN_TEMPLATE = '''"""Skeleton 3dfs import plugin for {repo_name}."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from three_dfs.import_plugins import register_plugin


class {class_name}:
    """Import plugin for {repo_name}.

    TODO: add authentication helpers, scraping logic, and metadata mapping.
    """

    def can_handle(self, source: str) -> bool:
        """Return ``True`` when *source* identifies a {repo_name} asset."""

        # TODO: Replace with repository specific detection logic.
        return source.startswith("{slug}:")

    def fetch(self, source: str, destination: Path) -> dict[str, Any]:
        """Download the asset represented by *source* into *destination*."""

        # TODO: Implement authentication, download/scraping, and metadata mapping.
        raise NotImplementedError("Implement remote fetch for {repo_name} assets")


register_plugin({class_name}())
'''
