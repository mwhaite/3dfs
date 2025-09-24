"""Helpers for constructing asset metadata payloads."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = ["build_asset_metadata"]


def build_asset_metadata(
    *,
    source: str | Path,
    source_type: str,
    managed_path: str | Path,
    original_path: str | Path | None = None,
    size: int | float | None = None,
    timestamps: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a metadata dictionary with normalized core asset fields.

    Parameters
    ----------
    source:
        Identifier for where the asset originated.
    source_type:
        Classifier describing how the asset was produced (``"local"``,
        ``"remote"``, ``"customization"``, etc.).
    managed_path:
        Filesystem location of the persisted asset under managed storage.
    original_path:
        Optional pointer to the original source material. When omitted the
        *source* value is reused.
    size:
        Known byte size for the managed asset. When omitted the size is read
        from ``managed_path``.
    timestamps:
        Mapping of timestamp keys (``"imported_at"``, ``"generated_at"``, ...)
        to values. Falsy values are ignored.
    extra:
        Additional key/value pairs merged into the resulting metadata.
    """

    original = original_path if original_path is not None else source
    metadata: dict[str, Any] = {
        "source": _stringify(source),
        "source_type": str(source_type),
        "original_path": _stringify(original),
        "managed_path": _stringify(managed_path),
        "size": _resolve_size(managed_path, size),
    }

    if timestamps:
        for key, value in timestamps.items():
            if value:
                metadata[str(key)] = _stringify(value)

    if extra:
        metadata.update(extra)

    return metadata


def _resolve_size(path: str | Path, explicit_size: int | float | None) -> int:
    if explicit_size is not None:
        return int(explicit_size)

    resolved = Path(path)
    return resolved.stat().st_size


def _stringify(value: str | Path | Any) -> str:
    return str(value)
