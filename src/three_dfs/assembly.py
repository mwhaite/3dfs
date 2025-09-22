"""Helpers for working with assembly metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

__all__ = ["discover_arrangement_scripts"]

ARRANGEMENT_DIR_NAMES: tuple[str, ...] = ("arrangements", "_arrangements")
ARRANGEMENT_NAME_HINTS: tuple[str, ...] = ("arrangement", "arrange", "layout")


def discover_arrangement_scripts(
    folder: Path,
    existing: Iterable[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return arrangement metadata discovered within *folder*.

    Arrangement scripts are OpenSCAD sources located inside a dedicated
    ``arrangements`` directory (or the legacy ``_arrangements`` variant).
    As a convenience, OpenSCAD files that live directly in *folder* are also
    treated as arrangements when their filename contains hints such as
    ``arrangement`` or ``layout``.

    Parameters
    ----------
    folder:
        Assembly root directory that may contain arrangement scripts.
    existing:
        Optional iterable of mappings describing previously stored arrangement
        metadata.  Any recognised entries are merged with the newly discovered
        files so user-specified labels or descriptions are preserved.
    """

    root = folder.expanduser().resolve(strict=False)
    existing_map = _index_existing(existing, root)

    discovered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for script in _iter_arrangement_scripts(root):
        normalized = _normalize_to_string(script, root)
        if normalized in seen:
            continue
        seen.add(normalized)
        base_entry = _base_entry_for(script, root)
        existing_entry = existing_map.pop(normalized, None)
        if existing_entry is not None:
            merged = dict(existing_entry)
            preserved_label = str(merged.get("label") or "").strip()
            merged.update(base_entry)
            if preserved_label:
                merged["label"] = preserved_label
            discovered.append(merged)
        else:
            discovered.append(base_entry)

    for normalized, entry in existing_map.items():
        candidate = Path(normalized)
        if not candidate.exists() or not candidate.is_file():
            continue
        merged = dict(entry)
        merged.setdefault("path", str(candidate))
        merged.setdefault("kind", "arrangement")
        rel_path = _relative_path(candidate, root)
        if rel_path is not None:
            merged.setdefault("rel_path", rel_path)
        if not str(merged.get("label") or "").strip():
            merged["label"] = _friendly_label(candidate)
        discovered.append(merged)

    discovered.sort(key=_arrangement_sort_key)
    return discovered


def _index_existing(
    entries: Iterable[Mapping[str, Any]] | None,
    root: Path,
) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    if not entries:
        return mapping

    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        normalized: str | None = None
        for key in ("path", "rel_path"):
            raw = entry.get(key)
            if isinstance(raw, str) and raw.strip():
                candidate = Path(raw)
                if candidate.suffix.lower() != ".scad":
                    continue
                normalized = _normalize_to_string(candidate, root)
                break
        if normalized is None:
            continue
        mapping[normalized] = dict(entry)
    return mapping


def _iter_arrangement_scripts(root: Path) -> Iterator[Path]:
    for name in ARRANGEMENT_DIR_NAMES:
        candidate = root / name
        if not candidate.exists() or not candidate.is_dir():
            continue
        yield from _iter_scad_files(candidate)

    if root.exists():
        for entry in root.iterdir():
            if entry.is_file() and entry.suffix.lower() == ".scad" and _looks_like_arrangement(entry):
                yield entry


def _iter_scad_files(directory: Path) -> Iterator[Path]:
    for entry in directory.rglob("*"):
        if entry.is_file() and entry.suffix.lower() == ".scad":
            yield entry


def _looks_like_arrangement(path: Path) -> bool:
    stem = path.stem.lower()
    return any(token in stem for token in ARRANGEMENT_NAME_HINTS)


def _base_entry_for(script: Path, root: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": str(script),
        "label": _friendly_label(script),
        "kind": "arrangement",
    }
    rel_path = _relative_path(script, root)
    if rel_path is not None:
        entry["rel_path"] = rel_path
    return entry


def _relative_path(target: Path, root: Path) -> str | None:
    try:
        return str(target.relative_to(root))
    except ValueError:
        return None


def _normalize_to_string(path: Path, root: Path) -> str:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return str(candidate.resolve(strict=False))


def _friendly_label(script: Path) -> str:
    stem = script.stem
    if not stem:
        return script.name
    normalized = stem.replace("_", " ").replace("-", " ").strip()
    if not normalized:
        return stem
    return normalized.title() if normalized.islower() else normalized


def _arrangement_sort_key(entry: Mapping[str, Any]) -> tuple[str, str]:
    primary = str(entry.get("rel_path") or entry.get("path") or "").casefold()
    label = str(entry.get("label") or "").casefold()
    return (primary, label)
