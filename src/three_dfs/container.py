"""Helpers for working with container metadata."""

from __future__ import annotations

import mimetypes
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:  # pragma: no cover - import for type checking only
    from .storage.repository import AssetRecord

from .container_metadata import ContainerMetadata, parse_container_metadata

__all__ = [
    "discover_arrangement_scripts",
    "build_arrangement_metadata",
    "build_attachment_metadata",
    "build_component_metadata",
    "build_linked_component_entry",
    "build_placeholder_metadata",
    "CONTAINER_METADATA_KEY",
    "get_container_metadata",
    "apply_container_metadata",
    "is_container_metadata",
    "is_container_asset",
]

ARRANGEMENT_DIR_NAMES: tuple[str, ...] = ("arrangements", "_arrangements")
ARRANGEMENT_NAME_HINTS: tuple[str, ...] = ("arrangement", "arrange", "layout")
CONTAINER_METADATA_KEY = "container_metadata"

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
    container_root: Path,
) -> dict[str, Any]:
    """Return enriched metadata for a container component."""

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
    metadata["container_path"] = _stringify(container_root)
    related_items = _merge_related_items(metadata.get("related_items"), container_root)
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


def build_linked_component_entry(
    source_component: Mapping[str, Any],
    source_container: AssetRecord,
    *,
    override_label: str | None = None,
) -> dict[str, Any]:
    """Return a component entry referencing a model from another container.

    The returned payload mirrors the structure produced by the container scan
    but marks the entry with ``kind == "linked_component"`` and embeds
    ``link_import`` metadata so downstream CRUD operations can trace the
    original source.
    """

    source_path_raw = source_component.get("path") if isinstance(source_component, Mapping) else None
    path_text = str(source_path_raw or "").strip()
    if not path_text:
        raise ValueError("Source component is missing a path")

    if not isinstance(source_component, Mapping):
        raise TypeError("source_component must be a mapping")

    try:
        asset_id_value = source_component.get("asset_id")
        asset_id = int(asset_id_value) if asset_id_value is not None else None
    except (TypeError, ValueError):
        asset_id = None

    raw_label = override_label or source_component.get("label")
    try:
        label = str(raw_label or "").strip()
    except Exception:
        label = ""
    if not label:
        try:
            label = Path(path_text).name
        except Exception:
            label = path_text

    metadata = dict(source_component.get("metadata") or {})
    link_import_id = str(uuid.uuid4())
    source_label = (
        source_container.metadata.get("display_name") if isinstance(source_container.metadata, Mapping) else None
    )
    if not isinstance(source_label, str) or not source_label.strip():
        source_label = source_container.label

    link_payload = {
        "link_import_id": link_import_id,
        "source_container_id": source_container.id,
        "source_container_path": source_container.path,
        "source_container_label": source_label,
        "source_component_path": path_text,
        "source_component_label": label,
        "source_component_relative_path": source_component.get("relative_path"),
        "source_asset_id": asset_id,
        "linked_at": datetime.now(UTC).isoformat(),
    }

    metadata["link_import"] = link_payload

    entry: dict[str, Any] = {
        "path": path_text,
        "label": label,
        "asset_id": asset_id,
        "kind": "linked_component",
        "metadata": metadata,
        "link_import_id": link_import_id,
    }

    relative_path = source_component.get("relative_path")
    if isinstance(relative_path, str) and relative_path.strip():
        entry["relative_path"] = relative_path.strip()

    suffix = source_component.get("suffix")
    if isinstance(suffix, str) and suffix:
        entry["suffix"] = suffix

    return entry


def build_attachment_metadata(
    attachment_path: Path | str,
    *,
    container_root: Path,
    source_path: Path | str | None = None,
    existing_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return metadata for an attachment stored within a container."""

    metadata = dict(existing_metadata or {})
    attachment_str = _stringify(attachment_path)
    metadata.setdefault("asset_path", attachment_str)
    metadata["container_path"] = _stringify(container_root)
    if source_path is not None:
        metadata.setdefault("original_path", _stringify(source_path))

    author = _extract_author(metadata)
    if author:
        metadata["author"] = author

    upstream_links = _gather_upstream_links(metadata)
    if upstream_links:
        metadata["upstream_links"] = upstream_links

    related_items = _merge_related_items(metadata.get("related_items"), container_root)
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


def build_arrangement_metadata(script_path: Path, container_root: Path) -> dict[str, Any]:
    """Return metadata describing an arrangement script."""

    metadata = {
        "asset_path": _stringify(script_path),
        "container_path": _stringify(container_root),
        "mime_type": "application/x-openscad",
        "handler": "openscad",
    }
    metadata["related_items"] = _merge_related_items(None, container_root)
    return metadata


def build_placeholder_metadata(path: Path, *, container_root: Path) -> dict[str, Any]:
    """Return metadata for a placeholder component directory."""

    metadata = {
        "asset_path": _stringify(path),
        "container_path": _stringify(container_root),
    }
    metadata["related_items"] = _merge_related_items(None, container_root)
    return metadata


def get_container_metadata(source: AssetRecord | Mapping[str, Any] | None) -> ContainerMetadata:
    """Return structured metadata for *source*."""

    if source is None:
        return parse_container_metadata(None)
    if isinstance(source, Mapping):
        if CONTAINER_METADATA_KEY in source:
            payload = source.get(CONTAINER_METADATA_KEY)
            if isinstance(payload, Mapping):
                return parse_container_metadata(payload)
        if _looks_like_container_metadata(source):
            return parse_container_metadata(source)
        return parse_container_metadata(None)
    metadata = getattr(source, "metadata", None)
    if isinstance(metadata, Mapping):
        payload = metadata.get(CONTAINER_METADATA_KEY)
        if isinstance(payload, Mapping):
            return parse_container_metadata(payload)
        if _looks_like_container_metadata(metadata):
            return parse_container_metadata(metadata)
    return parse_container_metadata(None)


def apply_container_metadata(
    base_metadata: Mapping[str, Any] | None,
    container_metadata: ContainerMetadata | Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a metadata dictionary embedding *container_metadata*."""

    merged = dict(base_metadata or {})
    if isinstance(container_metadata, ContainerMetadata):
        payload = container_metadata.to_dict()
    elif isinstance(container_metadata, Mapping):
        payload = ContainerMetadata.from_mapping(container_metadata).to_dict()
    else:
        payload = ContainerMetadata().to_dict()
    merged[CONTAINER_METADATA_KEY] = payload
    return merged


def _looks_like_container_metadata(payload: Mapping[str, Any]) -> bool:
    keys = {"printed_status", "priority", "contacts", "external_links", "due_date", "notes"}
    return any(key in payload for key in keys)


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
        Container root directory that may contain arrangement scripts.
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
            merged_metadata = _merge_metadata_dicts(base_entry.get("metadata"), existing_entry.get("metadata"))
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
        merged_metadata = _merge_metadata_dicts(build_arrangement_metadata(candidate, root), entry.get("metadata"))
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
        prefix = text[: text.rfind(token)].rstrip(" ").rstrip(":")
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
    container_root: Path,
) -> list[dict[str, str]]:
    entries = _normalize_related_entries(existing)
    container_path = _stringify(container_root)
    container_label = container_root.name or container_path
    container_entry = {
        "path": container_path,
        "label": container_label,
        "relationship": "container",
    }
    if not any(item.get("path") == container_entry["path"] for item in entries):
        entries.append(container_entry)
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
    path = _stringify_or_none(value.get("path") or value.get("target") or value.get("href"))
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


def _default_handler_for_path(path: Path | str, *, mime_type: str | None = None) -> str | None:
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


def is_container_metadata(metadata: Mapping[str, Any] | None) -> bool:
    """Return ``True`` when *metadata* describes a container asset."""

    if not isinstance(metadata, Mapping):
        return False
    kind = str(metadata.get("kind") or "").strip().lower()
    if kind == "container":
        return True
    container_path = metadata.get("container_path")
    if isinstance(container_path, str) and container_path.strip():
        return True
    components = metadata.get("components")
    if isinstance(components, list) and components:
        return True
    return False


def is_container_asset(asset: AssetRecord | None) -> bool:
    """Return ``True`` when *asset* references a container."""

    if asset is None:
        return False
    metadata = getattr(asset, "metadata", None)
    return is_container_metadata(metadata)
