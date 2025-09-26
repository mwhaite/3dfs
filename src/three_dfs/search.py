"""High level search helpers for assets, projects, and project items."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .storage import AssetRepository, AssetService, SQLiteStorage

__all__ = ["LibrarySearch", "SearchHit"]


SearchScope = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchHit:
    """Describe an item returned from :class:`LibrarySearch.search`."""

    scope: str
    """Domain of the result (``asset``, ``project``, ``component``, ``attachment``)."""

    path: str
    """Primary identifier for the hit (asset path or component path)."""

    label: str
    """Display label associated with :attr:`path`."""

    matched_fields: tuple[str, ...]
    """Sequence of field names that satisfied the search query."""

    tags: tuple[str, ...] = ()
    """Tag values considered during matching."""

    metadata: Mapping[str, object] | None = None
    """Optional metadata payload associated with the hit."""

    asset_id: int | None = None
    """Identifier for the originating asset when available."""

    project_path: str | None = None
    """Filesystem path of the parent project for component and attachment hits."""

    project_label: str | None = None
    """Display label of the parent project for component and attachment hits."""

    component_kind: str | None = None
    """Kind of project component (``component`` or ``placeholder``)."""


class LibrarySearch:
    """Search across assets, projects, components, and attachments."""

    _VALID_SCOPES: frozenset[str] = frozenset(
        {"asset", "project", "component", "attachment"}
    )

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        service: AssetService | None = None,
    ) -> None:
        if path is not None and service is not None:
            raise ValueError(
                "Provide either a database path or an AssetService, not both."
            )

        if service is None:
            storage = SQLiteStorage(path)
            repository = AssetRepository(storage)
            service = AssetService(repository)

        self._service = service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        scopes: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[SearchHit]:
        """Return results whose text matches *query* across *scopes*."""

        terms = _normalise_terms(query)
        if not terms:
            return []

        scope_filter = self._normalise_scopes(scopes)

        hits: list[SearchHit] = []
        for asset in self._service.list_assets():
            metadata = asset.metadata if isinstance(asset.metadata, Mapping) else {}
            tags = tuple(sorted(asset.tags, key=str.casefold))

            asset_scope = "project" if metadata.get("kind") == "project" else "asset"
            base_fields = {
                "label": [asset.label],
                "path": [asset.path],
                "tags": tags,
                "metadata": list(_iter_metadata_tokens(metadata)),
            }

            if asset_scope in scope_filter:
                matched, matched_fields = _matches(terms, base_fields)
                if matched:
                    hits.append(
                        SearchHit(
                            scope=asset_scope,
                            path=asset.path,
                            label=asset.label,
                            metadata=dict(metadata),
                            tags=tags,
                            asset_id=asset.id,
                            matched_fields=_ordered_fields(matched_fields),
                            project_path=(
                                asset.path if asset_scope == "project" else None
                            ),
                            project_label=(
                                asset.label if asset_scope == "project" else None
                            ),
                        )
                    )

            if metadata.get("kind") != "project":
                continue

            project_path = asset.path
            project_label = asset.label
            project_tags = tags

            if "component" in scope_filter:
                components = metadata.get("components") or []
                for entry in components:
                    if not isinstance(entry, Mapping):
                        continue
                    comp_path = str(entry.get("path") or "").strip()
                    comp_label = _normalise_label(
                        entry.get("label"), fallback=comp_path
                    )
                    comp_kind = str(entry.get("kind") or "component")
                    comp_metadata = (
                        dict(entry.get("metadata"))
                        if isinstance(entry.get("metadata"), Mapping)
                        else None
                    )
                    comp_tags = _merge_tags(project_tags, comp_metadata)
                    fields = {
                        "label": [comp_label],
                        "path": [comp_path],
                        "kind": [comp_kind],
                        "project": [project_label, project_path],
                        "tags": comp_tags,
                        "metadata": list(_iter_metadata_tokens(comp_metadata or {})),
                    }
                    matched, matched_fields = _matches(terms, fields)
                    if matched:
                        asset_id = entry.get("asset_id")
                        try:
                            comp_asset_id = (
                                int(asset_id) if asset_id is not None else None
                            )
                        except Exception:  # noqa: BLE001 - defensive conversion
                            comp_asset_id = None
                        hits.append(
                            SearchHit(
                                scope="component",
                                path=comp_path,
                                label=comp_label or comp_path,
                                metadata=comp_metadata,
                                tags=tuple(sorted(comp_tags, key=str.casefold)),
                                asset_id=comp_asset_id,
                                project_path=project_path,
                                project_label=project_label,
                                component_kind=comp_kind,
                                matched_fields=_ordered_fields(matched_fields),
                            )
                        )

            if "attachment" in scope_filter:
                attachments = metadata.get("attachments") or []
                for entry in attachments:
                    if not isinstance(entry, Mapping):
                        continue
                    att_path = str(entry.get("path") or "").strip()
                    if not att_path:
                        continue
                    att_label = _normalise_label(entry.get("label"), fallback=att_path)
                    att_metadata = (
                        dict(entry.get("metadata"))
                        if isinstance(entry.get("metadata"), Mapping)
                        else None
                    )
                    att_tags = _merge_tags(project_tags, att_metadata)
                    fields = {
                        "label": [att_label],
                        "path": [att_path],
                        "project": [project_label, project_path],
                        "tags": att_tags,
                        "metadata": list(_iter_metadata_tokens(att_metadata or {})),
                    }
                    matched, matched_fields = _matches(terms, fields)
                    if matched:
                        hits.append(
                            SearchHit(
                                scope="attachment",
                                path=att_path,
                                label=att_label or att_path,
                                metadata=att_metadata,
                                tags=tuple(sorted(att_tags, key=str.casefold)),
                                project_path=project_path,
                                project_label=project_label,
                                matched_fields=_ordered_fields(matched_fields),
                            )
                        )

        hits.sort(
            key=lambda hit: (
                -len(hit.matched_fields),
                _scope_rank(hit.scope),
                hit.label.casefold(),
                hit.path.casefold(),
            )
        )

        if limit is not None and limit >= 0:
            return hits[:limit]
        return hits

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _normalise_scopes(self, scopes: Iterable[str] | None) -> frozenset[str]:
        if scopes is None:
            return self._VALID_SCOPES
        normalized: set[str] = set()
        for scope in scopes:
            scope_str = str(scope).strip().casefold()
            if not scope_str:
                continue
            if scope_str not in self._VALID_SCOPES:
                raise ValueError(f"Unknown search scope: {scope}")
            normalized.add(scope_str)
        return frozenset(normalized or self._VALID_SCOPES)


def _normalise_terms(query: str) -> tuple[str, ...]:
    parts = [part.casefold() for part in str(query).split() if part.strip()]
    return tuple(parts)


def _normalise_label(value: object, *, fallback: str) -> str:
    try:
        label = str(value).strip() if value is not None else ""
    except Exception:  # noqa: BLE001 - metadata may contain non-string values
        label = ""
    if label:
        return label
    fallback_str = str(fallback or "").strip()
    if fallback_str:
        return fallback_str
    return ""


def _iter_metadata_tokens(
    metadata: Mapping[str, object] | Sequence[object] | object,
) -> Iterable[str]:
    if isinstance(metadata, Mapping):
        for key, value in metadata.items():
            key_str = str(key)
            if key_str:
                yield key_str
            yield from _iter_metadata_tokens(value)
    elif isinstance(metadata, (list, tuple, set, frozenset)):
        for item in metadata:
            yield from _iter_metadata_tokens(item)
    elif metadata is None:
        return
    else:
        text = str(metadata)
        if text:
            yield text


def _merge_tags(
    project_tags: Sequence[str], metadata: Mapping[str, object] | None
) -> tuple[str, ...]:
    merged: dict[str, None] = {str(tag): None for tag in project_tags if tag}
    if metadata is not None:
        raw_tags = metadata.get("tags") if isinstance(metadata, Mapping) else None
        if isinstance(raw_tags, (list, tuple, set, frozenset)):
            for tag in raw_tags:
                tag_str = str(tag).strip()
                if tag_str:
                    merged.setdefault(tag_str, None)
        elif isinstance(raw_tags, str):
            tag_str = raw_tags.strip()
            if tag_str:
                merged.setdefault(tag_str, None)
    return tuple(merged.keys())


def _matches(
    terms: tuple[str, ...], fields: Mapping[str, Sequence[str]]
) -> tuple[bool, set[str]]:
    field_texts: dict[str, str] = {}
    for name, values in fields.items():
        tokens = [str(value).casefold() for value in values if str(value).strip()]
        if not tokens:
            continue
        field_texts[name] = " ".join(tokens)

    if not field_texts:
        return False, set()

    matched_fields: set[str] = set()
    for term in terms:
        if not any(term in text for text in field_texts.values()):
            return False, set()
        for field_name, text in field_texts.items():
            if term in text:
                matched_fields.add(field_name)

    return True, matched_fields


def _ordered_fields(fields: set[str]) -> tuple[str, ...]:
    ordered = ["label", "path", "tags", "project", "kind", "metadata"]
    priority = {name: index for index, name in enumerate(ordered)}
    return tuple(
        sorted(fields, key=lambda name: (priority.get(name, len(priority)), name))
    )


def _scope_rank(scope: str) -> int:
    order = {"project": 0, "component": 1, "attachment": 2, "asset": 3}
    return order.get(scope, len(order))
