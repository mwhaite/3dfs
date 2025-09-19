"""Pytest configuration helpers for three_dfs tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the source directory is importable without requiring an editable install.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from three_dfs.config import configure


@pytest.fixture(autouse=True)
def reset_app_config() -> None:
    """Ensure each test runs with the default application configuration."""

    configure(library_root=None)
    yield
    configure(library_root=None)
