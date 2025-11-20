#!/usr/bin/env python3
"""Entry-point shim for frozen builds on all platforms."""

from __future__ import annotations

from three_dfs.app import main


if __name__ == "__main__":
    raise SystemExit(main())
