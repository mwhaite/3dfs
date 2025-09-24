"""Configuration helpers for the 3dfs application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .utils.paths import coerce_required_path

__all__ = [
    "AppConfig",
    "DEFAULT_LIBRARY_ROOT",
    "LIBRARY_ROOT_ENV_VAR",
    "configure",
    "get_config",
]

LIBRARY_ROOT_ENV_VAR: Final[str] = "THREE_DFS_LIBRARY_PATH"
"""Environment variable that overrides the default library location."""

DEFAULT_LIBRARY_ROOT: Final[Path] = Path.home() / "Models"
"""Default filesystem path where the asset library is stored."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Runtime configuration for the 3dfs application."""

    library_root: Path

    def __post_init__(self) -> None:
        normalized = coerce_required_path(self.library_root)
        object.__setattr__(self, "library_root", normalized)


_CONFIG: AppConfig | None = None


def get_config() -> AppConfig:
    """Return the cached :class:`AppConfig` instance."""

    global _CONFIG
    if _CONFIG is None:
        _CONFIG = _build_config()
    return _CONFIG


def configure(*, library_root: str | Path | None = None) -> AppConfig:
    """Rebuild the global configuration with optional overrides."""

    global _CONFIG
    _CONFIG = _build_config(library_root=library_root)
    return _CONFIG


def _build_config(*, library_root: str | Path | None = None) -> AppConfig:
    if library_root is not None:
        normalized = coerce_required_path(
            library_root,
            empty_error="Library path overrides cannot be empty",
        )
        return AppConfig(library_root=normalized)

    env_value = os.environ.get(LIBRARY_ROOT_ENV_VAR)
    if env_value:
        normalized = coerce_required_path(
            env_value,
            empty_error="Library path overrides cannot be empty",
        )
        return AppConfig(library_root=normalized)

    return AppConfig(library_root=DEFAULT_LIBRARY_ROOT)
