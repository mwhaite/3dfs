#!/usr/bin/env python3
"""Ensure every container asset carries structured metadata defaults."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

from three_dfs.container import (
    CONTAINER_METADATA_KEY,
    apply_container_metadata,
    get_container_metadata,
)
from three_dfs.storage import AssetRepository, SQLiteStorage, DEFAULT_DB_PATH
from three_dfs.storage import AssetRepository, SQLiteStorage, DEFAULT_DB_PATH


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Seed the container_metadata block for every container asset. "
            "Existing values are preserved; missing entries are populated with defaults."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Path to the 3dfs SQLite database (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the containers that would be updated without writing to the database.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every container update as it is processed.",
    )
    return parser.parse_args()


def _iter_containers(repository: AssetRepository) -> Iterable[int]:
    for record in repository.list_assets():
        metadata = record.metadata or {}
        if metadata.get("kind") == "container":
            yield record.id


def _migrate_container(repository: AssetRepository, asset_id: int, dry_run: bool, verbose: bool) -> bool:
    record = repository.get_asset(asset_id)
    if record is None:
        return False

    metadata = record.metadata or {}
    existing_payload = metadata.get(CONTAINER_METADATA_KEY)
    structured = get_container_metadata(record)
    desired_payload = structured.to_dict()

    if isinstance(existing_payload, dict) and existing_payload == desired_payload:
        if verbose:
            print(f"[skip] Container #{asset_id} already up to date.")
        return False

    new_metadata = apply_container_metadata(metadata, structured)
    if dry_run:
        print(f"[dry-run] Would update container #{asset_id} ({record.label}).")
        return True

    repository.update_asset(asset_id, metadata=new_metadata)
    if verbose:
        print(f"[update] Container #{asset_id} – metadata refreshed.")
    return True


def main() -> int:
    args = _parse_args()
    storage = SQLiteStorage(args.db)
    repository = AssetRepository(storage)

    updated = 0
    for asset_id in _iter_containers(repository):
        if _migrate_container(repository, asset_id, args.dry_run, args.verbose):
            updated += 1

    if args.dry_run:
        print(f"[dry-run] {updated} container(s) would be updated.")
    else:
        print(f"{updated} container(s) updated.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
