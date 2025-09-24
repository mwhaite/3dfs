"""Shared path utilities used across three_dfs modules."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

__all__ = ["resolve_storage_root"]


def resolve_storage_root(
    storage_root: Path | str | None,
    *,
    default: Path | Callable[[], Path],
) -> Path:
    """Return the managed storage root for importer-style operations.

    Parameters
    ----------
    storage_root:
        Optional override supplied by the caller. When provided the value is
        expanded, converted to an absolute path, and resolved. Relative paths
        are interpreted relative to the current working directory.
    default:
        Default managed storage location used when *storage_root* is omitted.
        The value can be a :class:`~pathlib.Path` instance or a zero-argument
        callable returning one. The callable form is useful when the default
        depends on configuration that should be resolved lazily.
    """

    if storage_root is None:
        return default() if callable(default) else default

    candidate = Path(storage_root).expanduser()
    if not candidate.is_absolute():
        candidate = candidate.resolve()
    return candidate
