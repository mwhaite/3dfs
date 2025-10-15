#!/usr/bin/env python3
"""Utility script to delete the 3dfs SQLite database for debugging."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from three_dfs.storage import DEFAULT_DB_PATH


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Delete the 3dfs assets SQLite database. Useful when you need to "
            "start with a clean catalog during debugging."
        )
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=(
            "Path to the SQLite database file. Defaults to the standard "
            f"location ({DEFAULT_DB_PATH})."
        ),
    )
    parser.add_argument(
        "--yes",
        dest="assume_yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be removed without deleting anything.",
    )
    return parser.parse_args()


def _delete_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def main() -> int:
    args = _parse_args()
    target = args.path.expanduser()

    if str(target) == ":memory:":
        print("In-memory databases do not need wiping.")
        return 0

    ancillary = [target.with_suffix(target.suffix + suffix) for suffix in ("-wal", "-shm")]

    existing = [path for path in [target, *ancillary] if path.exists()]

    if not existing:
        print(f"No database files found at {target}.")
        return 0

    if args.dry_run:
        print("[dry-run] Would remove:")
        for path in existing:
            print(f"  {path}")
        return 0

    if not args.assume_yes:
        prompt = (
            "This will permanently delete the 3dfs database at "
            f"{target}. Continue? [y/N] "
        )
        try:
            reply = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if reply not in {"y", "yes"}:
            print("Aborted.")
            return 1

    _delete_file(target)
    for path in ancillary:
        _delete_file(path)

    print("Deleted:")
    print(f"  {target}")
    for path in ancillary:
        if not path.exists():
            print(f"  {path}")

    parent = target.parent
    if parent.exists() and not any(parent.iterdir()):
        print(f"Directory {parent} is now empty.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
