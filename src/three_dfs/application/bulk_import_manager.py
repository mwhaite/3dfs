"""Bulk import manager for importing existing libraries."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import get_config
from ..importer import SUPPORTED_EXTENSIONS

if TYPE_CHECKING:
    from .main_window import MainWindow
    from ..ui.bulk_import_dialog import BulkImportDialog


logger = logging.getLogger(__name__)


class BulkImportManager:
    """Handles bulk import functionality for importing existing libraries."""

    def __init__(self, main_window: MainWindow) -> None:
        """Initialize the bulk import manager."""
        self._main_window = main_window

    def perform_bulk_import(self, dialog: BulkImportDialog) -> None:
        """Perform the bulk import based on the dialog settings."""
        source_dir_str = dialog.source_directory()
        if not source_dir_str:
            self._main_window.statusBar().showMessage("No source directory selected.", 3000)
            return

        source_dir = Path(source_dir_str)
        if not source_dir.exists() or not source_dir.is_dir():
            self._main_window.statusBar().showMessage(f"Source directory does not exist: {source_dir}", 3000)
            return

        # Get options from dialog
        one_container_per_model = dialog.one_container_per_model()
        use_path_for_tags = dialog.use_path_for_tags()
        flatten_structure = dialog.flatten_structure()
        include_subdirs = dialog.include_subdirectories()
        file_extensions = dialog.file_extensions()

        # If no extensions specified, use default supported extensions
        if not file_extensions:
            file_extensions = [ext.lower() for ext in SUPPORTED_EXTENSIONS]

        # Collect files to import
        files_to_import = self._collect_files(source_dir, file_extensions, include_subdirs, flatten_structure)

        if not files_to_import:
            self._main_window.statusBar().showMessage("No files found to import.", 3000)
            return

        self._main_window.statusBar().showMessage(f"Found {len(files_to_import)} files to import...", 5000)

        # Perform the import based on options
        if one_container_per_model:
            self._import_one_container_per_model(
                files_to_import, source_dir, use_path_for_tags, flatten_structure
            )
        else:  # All files to single container
            self._import_all_to_single_container(
                files_to_import, source_dir, use_path_for_tags
            )

        self._main_window.statusBar().showMessage(
            f"Bulk import completed: {len(files_to_import)} files imported.", 5000
        )

    def _collect_files(self, source_dir: Path, extensions: list[str], include_subdirs: bool, flatten: bool) -> list[Path]:
        """Collect files to import based on criteria."""
        files = []
        patterns = [f"*{ext}" for ext in extensions]

        if include_subdirs and not flatten:
            # Recursively find files in subdirectories but preserve structure
            for pattern in patterns:
                files.extend(source_dir.rglob(pattern))
        elif include_subdirs and flatten:
            # Find all files in subdirectories but treat them as if in root
            for pattern in patterns:
                files.extend(source_dir.rglob(pattern))
        else:
            # Only find files in the root directory
            for pattern in patterns:
                files.extend(source_dir.glob(pattern))

        # Filter to only files that exist and are actual files (not directories)
        files = [f for f in files if f.is_file()]
        return sorted(files)  # Sort for consistent processing order

    def _import_one_container_per_model(
        self, 
        files: list[Path], 
        source_dir: Path, 
        use_path_for_tags: bool, 
        flatten: bool
    ) -> None:
        """Import each file as a separate container."""
        from .container_manager import ContainerManager
        
        container_manager = self._main_window._container_manager
        
        for file_path in files:
            # Get the relative path from source_dir to use for naming/organization
            try:
                if flatten:
                    # Use filename only when flattening
                    container_name = file_path.stem
                else:
                    # Use the relative path structure to create meaningful names
                    rel_path = file_path.relative_to(source_dir)
                    # Create container name from path, replacing path separators with underscores
                    container_name = str(rel_path.parent).replace("/", "_").replace("\\", "_")
                    if container_name == ".":
                        container_name = file_path.stem
                    else:
                        container_name = f"{container_name}_{file_path.stem}"
            except ValueError:
                # If the file is not relative to source_dir, just use the filename
                container_name = file_path.stem

            # Create a new container for this file
            container_folder = source_dir.parent / "bulk_import" / container_name
            container_folder.mkdir(parents=True, exist_ok=True)
            
            # Copy the file to the container
            dest_file = container_folder / file_path.name
            if not dest_file.exists():
                shutil.copy2(file_path, dest_file)
            
            # Add to database as container
            label = f"Container: {container_name}"
            metadata: dict[str, Any] = {
                "kind": "container",
                "container_type": "container",
                "display_name": container_name,
                "created_from_bulk_import": True,
            }
            
            # Add tags if requested
            if use_path_for_tags:
                # Use the relative directory structure as tags
                try:
                    rel_path = file_path.relative_to(source_dir.parent)
                    path_parts = list(rel_path.parts[:-1])  # Exclude filename
                    if path_parts:
                        metadata["tags"] = ",".join(path_parts)
                except ValueError:
                    pass  # File is not relative to source_dir, skip tags

            # Add the file as a component in the container
            component_entry = {
                "path": str(dest_file),
                "label": file_path.name,
                "kind": "file",
            }
            metadata["components"] = [component_entry]

            # Create asset record in database
            existing = self._main_window._asset_service.get_asset_by_path(str(container_folder))
            if existing is None:
                created_asset = self._main_window._asset_service.create_asset(
                    str(container_folder),
                    label=label,
                    metadata=metadata,
                )
            else:
                created_asset = self._main_window._asset_service.update_asset(
                    existing.id,
                    label=label,
                    metadata=metadata,
                )

            # Refresh repository to show the new container
            self._main_window._populate_repository()

    def _import_all_to_single_container(self, files: list[Path], source_dir: Path, use_path_for_tags: bool) -> None:
        """Import all files to a single container."""
        from .container_manager import ContainerManager
        
        container_manager = self._main_window._container_manager
        
        # Create a single container for all files
        container_name = f"bulk_import_{source_dir.name}"
        container_folder = source_dir.parent / container_name
        container_folder.mkdir(parents=True, exist_ok=True)
        
        # Prepare metadata for the container
        metadata: dict[str, Any] = {
            "kind": "container",
            "container_type": "container",
            "display_name": container_name,
            "created_from_bulk_import": True,
        }
        
        # Add tags if requested
        if use_path_for_tags:
            metadata["tags"] = source_dir.name  # Use source directory name as tag

        # Add all files as components in the container
        components = []
        for file_path in files:
            # Copy the file to the container
            dest_file = container_folder / file_path.name
            if not dest_file.exists():
                # Handle duplicate filenames by appending numbers
                counter = 1
                original_dest_file = dest_file
                while dest_file.exists():
                    name_part = original_dest_file.stem
                    ext_part = original_dest_file.suffix
                    dest_file = container_folder / f"{name_part}_{counter}{ext_part}"
                    counter += 1
                
                shutil.copy2(file_path, dest_file)
            
            component_entry = {
                "path": str(dest_file),
                "label": dest_file.name,
                "kind": "file",
            }
            components.append(component_entry)

        metadata["components"] = components

        # Create or update asset record in database
        label = f"Container: {container_name}"
        existing = self._main_window._asset_service.get_asset_by_path(str(container_folder))
        if existing is None:
            created_asset = self._main_window._asset_service.create_asset(
                str(container_folder),
                label=label,
                metadata=metadata,
            )
        else:
            created_asset = self._main_window._asset_service.update_asset(
                existing.id,
                label=label,
                metadata=metadata,
            )

        # Refresh repository to show the new container
        self._main_window._populate_repository()