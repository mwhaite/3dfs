from __future__ import annotations

from pathlib import Path

import pytest

from three_dfs.config import (
    DEFAULT_LIBRARY_ROOT,
    LIBRARY_ROOT_ENV_VAR,
    configure,
    get_config,
)


def test_default_library_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default configuration should resolve to the ~/Models directory."""

    monkeypatch.delenv(LIBRARY_ROOT_ENV_VAR, raising=False)
    configure(library_root=None)
    config = get_config()

    assert config.library_root == DEFAULT_LIBRARY_ROOT


def test_configure_overrides_library_root(tmp_path: Path) -> None:
    """Explicit overrides should update the cached configuration."""

    override = tmp_path / "library"
    configure(library_root=override)
    config = get_config()

    assert config.library_root == override.resolve()


def test_environment_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An environment variable should control the default library path."""

    override = tmp_path / "env_library"
    monkeypatch.setenv(LIBRARY_ROOT_ENV_VAR, str(override))
    configure(library_root=None)

    config = get_config()
    assert config.library_root == override.resolve()
