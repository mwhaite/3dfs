"""Pytest configuration helpers for three_dfs tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the source directory is importable without requiring an editable install.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
