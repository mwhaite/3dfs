"""Helpers for working with assembly metadata."""

from __future__ import annotations

import mimetypes
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:  # pragma: no cover - import for type checking only
    from .storage.repository import AssetRecord

__all__ = [
    "discover_arrangement_scripts",
    "build_arrangement_metadata",
    "build_attachment_metadata",
    "build_component_metadata",
    "build_placeholder_metadata",
]

ARRANGEMENT_DIR_NAMES: tuple[str, ...] = ("arrangements", "_arrangements")
ARRANGEMENT_NAME_HINTS: tuple[str, ...] = ("arrangement", "arrange", "layout")

_UPSTREAM_KEYS: tuple[tuple[str, str], ...] = (
    ("source_url", "Source"),
    ("homepage", "Homepage"),
    ("documentation", "Documentation"),
    ("repository", "Repository"),
    ("source", "Source"),
    ("original_path", "Original"),
)


def build_component_metadata(
    record: AssetRecord,
    *,
    assembly_root: Path,
) -> dict[str, Any]:
    """Return enriched metadata for an assembly component."""

    base_metadata = getattr(record, "metadata", {}) or {}
    metadata = dict(base_metadata)

    asset_path = _stringify(getattr(record, "path", ""))
    if asset_path:
        metadata.setdefault("asset_path", asset_path)
    asset_label = _stringify(getattr(record, "label", ""))
    if asset_label:
        metadata.setdefault("asset_label", asset_label)
    asset_id = getattr(record, "id", None)
    if asset_id is not None:
        metadata["asset_id"] = int(asset_id)

    author = _extract_author(metadata)
    if author:
        metadata["author"] = author

    upstream_links = _gather_upstream_links(metadata)
    if upstream_links:
        metadata["upstream_links"] = upstream_links

    metadata["assembly_path"] = _stringify(assembly_root)
    related_items = _merge_related_items(metadata.get("related_items"), assembly_root)
    if related_items:
        metadata["related_items"] = related_items

    mime_type, _ = mimetypes.guess_type(asset_path)
    if mime_type:
        metadata["mime_type"] = mime_type

    handler = _stringify_or_none(metadata.get("handler"))
    if not handler:
        handler = _default_handler_for_path(asset_path, mime_type=mime_type)
        if handler:
            metadata["handler"] = handler

    return metadata


def build_attachment_metadata(
    attachment_path: Path | str,
    *,
    assembly_root: Path,
    source_path: Path | str | None = None,
    existing_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return metadata for an attachment stored within an assembly."""

    metadata = dict(existing_metadata or {})
    attachment_str = _stringify(attachment_path)
    metadata.setdefault("asset_path", attachment_str)
    metadata["assembly_path"] = _stringify(assembly_root)
    if source_path is not None:
        metadata.setdefault("original_path", _stringify(source_path))

    author = _extract_author(metadata)
    if author:
        metadata["author"] = author

    upstream_links = _gather_upstream_links(metadata)
    if upstream_links:
        metadata["upstream_links"] = upstream_links

    related_items = _merge_related_items(metadata.get("related_items"), assembly_root)
    if related_items:
        metadata["related_items"] = related_items

    mime_type, _ = mimetypes.guess_type(attachment_str)
    if mime_type:
        metadata["mime_type"] = mime_type

    handler = _stringify_or_none(metadata.get("handler"))
    if not handler:
        handler = _default_handler_for_path(attachment_str, mime_type=mime_type)
        if handler:
            metadata["handler"] = handler

    return metadata


def build_arrangement_metadata(
    script_path: Path, assembly_root: Path
) -> dict[str, Any]:
    """Return metadata describing an arrangement script."""

    metadata = {
        "asset_path": _stringify(script_path),
        "assembly_path": _stringify(assembly_root),
        "mime_type": "application/x-openscad",
        "handler": "openscad",
    }
    metadata["related_items"] = _merge_related_items(None, assembly_root)
    return metadata


def build_placeholder_metadata(path: Path, *, assembly_root: Path) -> dict[str, Any]:
    """Return metadata for a placeholder component directory."""

    metadata = {
        "asset_path": _stringify(path),
        "assembly_path": _stringify(assembly_root),
    }
    metadata["related_items"] = _merge_related_items(None, assembly_root)
    return metadata


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
            merged_metadata = _merge_metadata_dicts(
                base_entry.get("metadata"), existing_entry.get("metadata")
            )
            if merged_metadata:
                merged["metadata"] = merged_metadata
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
        merged_metadata = _merge_metadata_dicts(
            build_arrangement_metadata(candidate, root), entry.get("metadata")
        )
        if merged_metadata:
            merged["metadata"] = merged_metadata
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
            if (
                entry.is_file()
                and entry.suffix.lower() == ".scad"
                and _looks_like_arrangement(entry)
            ):
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
    entry["metadata"] = build_arrangement_metadata(script, root)
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


def _stringify(value: Path | str | Any) -> str:
    return str(value)


def _stringify_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_author(metadata: Mapping[str, Any]) -> str | None:
    for key in ("author", "creator", "designer", "artist", "owner"):
        text = _stringify_or_none(metadata.get(key))
        if text:
            return text
    return None


def _gather_upstream_links(metadata: Mapping[str, Any]) -> list[dict[str, str]]:
    existing = _normalize_link_entries(metadata.get("upstream_links"))
    derived: list[dict[str, str]] = []
    for key, label in _UPSTREAM_KEYS:
        url = _extract_link_url(metadata.get(key))
        if not url:
            continue
        entry: dict[str, str] = {"url": url}
        if label:
            entry["label"] = label
        derived.append(entry)

    combined = existing + derived
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in combined:
        url = _stringify_or_none(entry.get("url"))
        if not url or url in seen:
            continue
        normalized = {"url": url}
        label = _stringify_or_none(entry.get("label"))
        if label:
            normalized["label"] = label
        seen.add(url)
        unique.append(normalized)
    return unique


def _normalize_link_entries(value: object) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    if value is None:
        return entries
    if isinstance(value, Mapping):
        normalized = _normalize_link_mapping(value)
        if normalized:
            entries.append(normalized)
        return entries
    if isinstance(value, str):
        normalized = _normalize_link_string(value)
        if normalized:
            entries.append(normalized)
        return entries
    if isinstance(value, Sequence):
        for element in value:
            entries.extend(_normalize_link_entries(element))
    return entries


def _normalize_link_mapping(value: Mapping[str, Any]) -> dict[str, str] | None:
    url = _extract_link_url(value.get("url") or value.get("href") or value.get("path"))
    if not url:
        return None
    entry: dict[str, str] = {"url": url}
    label = _stringify_or_none(value.get("label") or value.get("name"))
    if label:
        entry["label"] = label
    return entry


def _normalize_link_string(value: str) -> dict[str, str] | None:
    text = value.strip()
    if not text:
        return None

    parts = text.replace("\n", " ").split()
    for token in reversed(parts):
        url = _extract_link_url(token)
        if not url:
            continue
        prefix = text[: text.rfind(token)].rstrip(" :")
        entry: dict[str, str] = {"url": url}
        if prefix:
            entry["label"] = prefix
        return entry

    url = _extract_link_url(text)
    if url:
        return {"url": url}
    return None


def _extract_link_url(value: Any) -> str | None:
    text = _stringify_or_none(value)
    if text is None:
        return None
    if _looks_like_url(text):
        return text
    return None


def _looks_like_url(text: str) -> bool:
    parsed = urlparse(text)
    if not parsed.scheme:
        return False
    if len(parsed.scheme) == 1 and text[1:2] == ":":
        # Windows drive path (e.g. C:\foo)
        return False
    return bool(parsed.netloc or parsed.path)


def _merge_related_items(
    existing: object,
    assembly_root: Path,
) -> list[dict[str, str]]:
    entries = _normalize_related_entries(existing)
    assembly_path = _stringify(assembly_root)
    assembly_label = assembly_root.name or assembly_path
    assembly_entry = {
        "path": assembly_path,
        "label": assembly_label,
        "relationship": "assembly",
    }
    if not any(item.get("path") == assembly_entry["path"] for item in entries):
        entries.append(assembly_entry)
    return entries


def _normalize_related_entries(value: object) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    if value is None:
        return entries
    if isinstance(value, Mapping):
        normalized = _related_entry_from_mapping(value)
        if normalized:
            entries.append(normalized)
        return entries
    if isinstance(value, str):
        normalized = _related_entry_from_string(value)
        if normalized:
            entries.append(normalized)
        return entries
    if isinstance(value, Sequence):
        for element in value:
            entries.extend(_normalize_related_entries(element))
    return entries


def _related_entry_from_mapping(value: Mapping[str, Any]) -> dict[str, str] | None:
    path = _stringify_or_none(
        value.get("path") or value.get("target") or value.get("href")
    )
    if not path:
        return None
    label = _stringify_or_none(value.get("label") or value.get("name"))
    if not label:
        try:
            label = Path(path).name
        except Exception:
            label = path
    entry: dict[str, str] = {"path": path, "label": label}
    relationship = _stringify_or_none(value.get("relationship") or value.get("type"))
    if relationship:
        entry["relationship"] = relationship
    return entry


def _related_entry_from_string(value: str) -> dict[str, str] | None:
    text = value.strip()
    if not text:
        return None
    try:
        label = Path(text).name
    except Exception:
        label = text
    return {"path": text, "label": label}


def _default_handler_for_path(
    path: Path | str, *, mime_type: str | None = None
) -> str | None:
    suffix = ""
    try:
        suffix = Path(path).suffix.lower()
    except Exception:
        suffix = str(path).lower()
    if suffix == ".scad":
        return "openscad"
    return "system"


def _merge_metadata_dicts(
    base: Mapping[str, Any] | None,
    override: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    result: dict[str, Any] = {}
    if base:
        result.update(base)
    if override:
        result.update(override)
    return result or None
