"""Pytest configuration helpers for three_dfs tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

try:  # pragma: no cover - dependency availability varies between environments
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - used when Qt is unavailable
    QApplication = None  # type: ignore[assignment]

# Ensure the source directory is importable without requiring an editable install.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture(autouse=True)
def reset_app_config() -> None:
    """Ensure each test runs with the default application configuration."""

    from three_dfs.config import configure

    configure(library_root=None)
    yield
    configure(library_root=None)


@pytest.fixture(scope="session")
def qapp():
    """Provide a ``QApplication`` instance for UI-oriented tests."""

    if QApplication is None:
        pytest.skip("PySide6 is unavailable in this environment")

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
