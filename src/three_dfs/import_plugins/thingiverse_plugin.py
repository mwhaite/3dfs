"""Importer plugin that fetches assets from Thingiverse."""

from __future__ import annotations

import json
import os
import re
import shutil
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from . import ImportPlugin, register_plugin


class ThingiverseImportPlugin(ImportPlugin):
    """Fetch containers hosted on Thingiverse via the public API."""

    API_ROOT = "https://api.thingiverse.com"
    WEB_ROOT = "https://www.thingiverse.com"
    USER_AGENT = "three-dfs-thingiverse-plugin/1.0"
    TOKEN_ENV_VAR = "THINGIVERSE_TOKEN"

    _THING_PATTERN = re.compile(r"thing:(?P<id>\d+)", re.IGNORECASE)
    _THING_PATH_PATTERN = re.compile(r"/things?/(?P<id>\d+)")

    _SUPPORTED_SUFFIXES = {
        ".fbx",
        ".gltf",
        ".glb",
        ".obj",
        ".ply",
        ".step",
        ".stl",
        ".stp",
    }

    def __init__(
        self,
        *,
        token: str | None = None,
        opener: Callable[[urllib.request.Request], Any] | None = None,
    ) -> None:
        self._token = token
        self._opener = opener or urllib.request.urlopen

    def can_handle(self, source: str) -> bool:
        return self._extract_thing_id(source) is not None

    def fetch(self, source: str, destination: Path) -> dict[str, Any]:
        thing_id = self._extract_thing_id(source)
        if thing_id is None:
            message = "Thingiverse plugin requires a valid thing identifier."
            raise RuntimeError(message)

        token = self._token or os.environ.get(self.TOKEN_ENV_VAR)
        if not token:
            message = (
                "Thingiverse API token missing. Set the THINGIVERSE_TOKEN "
                "environment variable or provide a token to the plugin."
            )
            raise RuntimeError(message)

        thing_endpoint = f"{self.API_ROOT}/things/{thing_id}"
        files_endpoint = f"{thing_endpoint}/files"

        thing_payload = self._get_json(thing_endpoint, token)
        files_payload = self._get_json(files_endpoint, token)

        primary_file = self._select_primary_file(files_payload)
        if primary_file is None:
            message = "Thingiverse listing does not expose files with supported " "extensions."
            raise RuntimeError(message)

        download_url = self._file_download_url(primary_file)
        if not download_url:
            message = "Unable to locate a download URL for the selected file."
            raise RuntimeError(message)

        self._download_file(download_url, destination, token)

        filename = str(primary_file.get("name") or f"thing_{thing_id}.stl")
        extension = Path(filename).suffix
        if not extension:
            extension = self._infer_extension_from_url(download_url)
            if extension:
                filename = f"{filename}{extension}"
        extension = extension.lower()

        files_metadata = self._build_files_metadata(files_payload, primary_file)

        metadata: dict[str, Any] = {
            "label": thing_payload.get("name") or f"Thingiverse {thing_id}",
            "filename": filename,
            "extension": extension or ".stl",
            "thingiverse": {
                "id": int(thing_id),
                "name": thing_payload.get("name"),
                "url": f"{self.WEB_ROOT}/thing:{thing_id}",
                "creator": self._extract_creator(thing_payload.get("creator")),
                "license": (thing_payload.get("license") or thing_payload.get("license_name")),
                "tags": self._extract_tags(thing_payload.get("tags", [])),
                "summary": thing_payload.get("description"),
                "thumbnail": thing_payload.get("thumbnail"),
                "files": files_metadata,
            },
            "source_links": {
                "thingiverse": f"{self.WEB_ROOT}/thing:{thing_id}",
            },
        }

        return metadata

    def _extract_thing_id(self, source: str) -> str | None:
        if not source:
            return None

        match = self._THING_PATTERN.fullmatch(source)
        if match:
            return match.group("id")

        try:
            parsed = urllib.parse.urlparse(source)
        except Exception:
            parsed = None

        if parsed and parsed.netloc and "thingiverse" in parsed.netloc.lower():
            path_match = self._THING_PATTERN.search(parsed.path or "")
            if path_match:
                return path_match.group("id")
            alternate_match = self._THING_PATH_PATTERN.search(parsed.path)
            if alternate_match:
                return alternate_match.group("id")

        if source.isdigit():
            return source

        return None

    def _get_json(self, url: str, token: str) -> Any:
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": self.USER_AGENT,
            },
        )
        with self._opener(request) as response:
            payload = response.read()

        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise RuntimeError(f"Invalid JSON response from {url!s}") from exc

    def _download_file(self, url: str, destination: Path, token: str) -> None:
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": self.USER_AGENT,
            },
        )
        with self._opener(request) as response, destination.open("wb") as target:
            shutil.copyfileobj(response, target)

    def _select_primary_file(self, files: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
        supported: list[Mapping[str, Any]] = []
        for file_info in files:
            name = str(file_info.get("name") or "")
            if Path(name).suffix.lower() in self._SUPPORTED_SUFFIXES:
                supported.append(file_info)

        if not supported:
            return None

        def sort_key(item: Mapping[str, Any]) -> tuple[int, int]:
            primary_flag = int(bool(item.get("is_primary") or item.get("default")))
            identifier = int(item.get("id") or 0)
            return (-primary_flag, identifier)

        supported.sort(key=sort_key)
        return supported[0]

    def _file_download_url(self, file_info: Mapping[str, Any]) -> str:
        return str(file_info.get("download_url") or file_info.get("direct_url") or file_info.get("url") or "")

    def _infer_extension_from_url(self, url: str) -> str:
        suffix = Path(urllib.parse.urlparse(url).path).suffix
        return suffix.lower()

    def _build_files_metadata(
        self,
        files: Iterable[Mapping[str, Any]],
        primary: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        primary_id = primary.get("id")
        result: list[dict[str, Any]] = []
        for file_info in files:
            entry = {
                "id": file_info.get("id"),
                "name": file_info.get("name"),
                "size": file_info.get("size"),
                "public_url": file_info.get("public_url"),
                "download_url": file_info.get("download_url"),
                "primary": file_info.get("id") == primary_id,
            }
            extension = Path(str(file_info.get("name") or "")).suffix.lower()
            if extension:
                entry["extension"] = extension
            result.append(entry)
        return result

    def _extract_creator(self, creator_payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not creator_payload:
            return None

        return {
            "id": creator_payload.get("id"),
            "name": creator_payload.get("name"),
            "url": creator_payload.get("url"),
            "public_url": creator_payload.get("public_url"),
        }

    def _extract_tags(self, tags_payload: Iterable[Mapping[str, Any]]) -> list[str]:
        tags: list[str] = []
        for entry in tags_payload:
            name = entry.get("name")
            if isinstance(name, str):
                tags.append(name)
        return tags


register_plugin(ThingiverseImportPlugin())
