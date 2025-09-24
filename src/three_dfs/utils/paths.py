"""Utilities for coercing user-provided values into :class:`~pathlib.Path` objects."""

from __future__ import annotations

from os import PathLike
from pathlib import Path
from typing import Any

__all__ = ["coerce_optional_path", "coerce_required_path"]


def _normalize_path(path: Path) -> Path:
    return path.expanduser().resolve()


def coerce_required_path(
    value: str | Path | PathLike[str],
    *,
    empty_error: str | None = None,
) -> Path:
    """Return *value* coerced into an absolute :class:`~pathlib.Path`.

    Parameters
    ----------
    value:
        Path-like object that must resolve to a non-empty filesystem location.
    empty_error:
        Optional custom error message raised when *value* cannot be coerced
        because it resolves to an empty string.
    """

    if isinstance(value, Path):
        candidate = value
    else:
        text = str(value).strip()
        if not text:
            msg = empty_error or "Path value cannot be empty."
            raise ValueError(msg)
        candidate = Path(text)

    return _normalize_path(candidate)


def coerce_optional_path(candidate: Any) -> Path | None:
    """Coerce *candidate* into a :class:`~pathlib.Path` when possible.

    Returns ``None`` when the provided value cannot be interpreted as a path.
    """

    if isinstance(candidate, Path):
        return _normalize_path(candidate)
    if isinstance(candidate, str | PathLike):
        text = str(candidate).strip()
        if text:
            return _normalize_path(Path(text))
    return None
