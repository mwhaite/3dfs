"""Helpers for evaluating relationships between customized assets and sources."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..utils.paths import coerce_optional_path

__all__ = ["CustomizationStatus", "evaluate_customization_status"]


@dataclass(slots=True)
class CustomizationStatus:
    """Represent the linkage between a customization and its source."""

    base_path: Path | None
    recorded_source_mtime: datetime | None
    current_source_mtime: datetime | None
    is_outdated: bool
    reason: str


def evaluate_customization_status(
    metadata: Mapping[str, Any],
    *,
    base_path: str | Path | None = None,
) -> CustomizationStatus:
    """Return the status of a customization relative to its source."""

    resolved_base: Path | None = _resolve_base_path(metadata, base_path)
    recorded = _parse_datetime(metadata.get("source_modified_at"))
    current: datetime | None = None
    reason = "Status unknown"
    is_outdated = False

    if resolved_base is None:
        reason = "Base source path unavailable."
    else:
        try:
            stat = resolved_base.stat()
        except OSError:
            reason = "Base source file is missing."
            is_outdated = True
        else:
            current = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            if recorded is not None:
                if current > recorded:
                    is_outdated = True
                    reason = "Base source updated since customization."
                else:
                    reason = "In sync with base source."
            else:
                reason = "Recorded source timestamp unavailable."

    return CustomizationStatus(resolved_base, recorded, current, is_outdated, reason)


def _resolve_base_path(
    metadata: Mapping[str, Any],
    base_path: str | Path | None,
) -> Path | None:
    if base_path is not None:
        return coerce_optional_path(base_path)

    candidates = (
        metadata.get("base_asset_path"),
        metadata.get("source"),
        metadata.get("base_asset"),
    )
    for candidate in candidates:
        path = coerce_optional_path(candidate)
        if path is not None:
            return path
    return None


def _parse_datetime(candidate: Any) -> datetime | None:
    if isinstance(candidate, datetime):
        return candidate
    if not isinstance(candidate, str):
        return None
    text = candidate.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
