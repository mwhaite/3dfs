"""Importer for MyMiniFactory."""

from __future__ import annotations

import re
from pathlib import Path

import requests

from ..application.settings import AppSettings
from ..config import get_config
from .base import Importer


class MyMiniFactoryImporter(Importer):
    """Importer for MyMiniFactory."""

    def import_container(self, url: str, settings: AppSettings) -> Path:
        """Import a container from a given URL."""
        access_token = settings.myminifactory_token
        if not access_token:
            raise ValueError(
                "MYMINIFACTORY_TOKEN not set in settings. "
                "Please obtain a token from the MyMiniFactory Developer Console and set it in the settings."
            )

        match = re.search(r"object/([\w-]+)-(\d+)", url)
        if not match:
            raise ValueError("Invalid MyMiniFactory URL. Expected format: .../object/OBJECT_NAME-OBJECT_ID")

        object_id = match.group(2)

        headers = {"Authorization": f"Bearer {access_token}"}

        # Get object details
        object_url = f"https://www.myminifactory.com/api/v2/objects/{object_id}"
        object_response = requests.get(object_url, headers=headers)
        object_response.raise_for_status()
        object_data = object_response.json()

        title = object_data.get("name", f"MyMiniFactory_{object_id}")

        config = get_config()
        root = config.library_root

        container_dir = root / title
        container_dir.mkdir(parents=True, exist_ok=True)

        # Get file details
        files_url = f"https://www.myminifactory.com/api/v2/objects/{object_id}/files"
        files_response = requests.get(files_url, headers=headers)
        files_response.raise_for_status()
        files_data = files_response.json()

        downloaded_count = 0
        for item in files_data.get("items", []):
            file_name = item.get("filename")
            download_url = item.get("download_url")

            if file_name and download_url:
                file_path = container_dir / file_name
                with requests.get(download_url, headers=headers, stream=True) as r:
                    r.raise_for_status()
                    with open(file_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                downloaded_count += 1

        print(f"Downloaded {downloaded_count} files to {container_dir}")
        return container_dir
