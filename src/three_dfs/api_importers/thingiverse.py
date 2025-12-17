"""Importer for Thingiverse."""

from __future__ import annotations

import re
from pathlib import Path

import requests

from ..application.settings import AppSettings
from ..config import get_config
from .base import Importer


class ThingiverseImporter(Importer):
    """Importer for Thingiverse."""

    def import_container(self, url: str, settings: AppSettings) -> Path:
        """Import a container from a given URL."""
        access_token = settings.thingiverse_token
        if not access_token:
            raise ValueError(
                "THINGIVERSE_TOKEN not set in settings. "
                "Please obtain a token from the Thingiverse Developer Console and set it in the settings."
            )

        match = re.search(r"thing:(\d+)", url)
        if not match:
            raise ValueError("Invalid Thingiverse URL. Expected format: .../thing:THING_ID")
        thing_id = match.group(1)

        # Get thing details
        thing_url = f"https://api.thingiverse.com/things/{thing_id}?access_token={access_token}"
        thing_response = requests.get(thing_url)
        thing_response.raise_for_status()
        thing_data = thing_response.json()

        title = thing_data.get("name", f"Thingiverse_{thing_id}")

        config = get_config()
        root = config.library_root

        container_dir = root / title
        container_dir.mkdir(parents=True, exist_ok=True)

        # Get file details
        files_url = f"https://api.thingiverse.com/things/{thing_id}/files?access_token={access_token}"
        files_response = requests.get(files_url)
        files_response.raise_for_status()
        files_data = files_response.json()

        downloaded_count = 0
        for file_info in files_data:
            file_name = file_info.get("name")
            download_url = file_info.get("download_url")

            if file_name and download_url:
                file_path = container_dir / file_name
                # Need to use the final download URL
                final_download_url_response = requests.get(
                    f"{download_url}?access_token={access_token}", allow_redirects=False
                )
                final_download_url_response.raise_for_status()
                final_download_url = final_download_url_response.headers.get("Location")

                if final_download_url:
                    with requests.get(final_download_url, stream=True) as r:
                        r.raise_for_status()
                        with open(file_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                f.write(chunk)
                    downloaded_count += 1

        print(f"Downloaded {downloaded_count} files to {container_dir}")
        return container_dir
