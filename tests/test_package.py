"""Basic smoke tests for the 3dfs package."""

from __future__ import annotations

import importlib


def test_package_importable() -> None:
    """Ensure that the top-level package can be imported."""

    module = importlib.import_module("three_dfs")
    assert module.__version__ == "0.1.0"
