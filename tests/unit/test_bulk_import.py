#!/usr/bin/env python3
"""Test script to verify the bulk import functionality."""

import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from three_dfs.ui.bulk_import_dialog import BulkImportDialog


def test_bulk_import_dialog():
    """Test the bulk import dialog creation and functionality."""
    app = QApplication(sys.argv)
    
    # Create a temporary directory structure to test with
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create some test files
        test_files = []
        for i in range(3):
            test_file = temp_path / f"test_model_{i}.stl"
            test_file.write_text(f"Mock STL content for test_model_{i}")
            test_files.append(test_file)
        
        # Create a subdirectory with more files
        subdir = temp_path / "subdir"
        subdir.mkdir()
        for i in range(2):
            test_file = subdir / f"nested_model_{i}.obj"
            test_file.write_text(f"Mock OBJ content for nested_model_{i}")
            test_files.append(test_file)
        
        print(f"Created test files: {test_files}")
        
        # Create and test the dialog
        dialog = BulkImportDialog()
        dialog._source_input.setText(str(temp_path))
        
        # Test the basic functionality
        print(f"Source directory: {dialog.source_directory()}")
        print(f"One container per model: {dialog.one_container_per_model()}")
        print(f"Use path for tags: {dialog.use_path_for_tags()}")
        print(f"Include subdirectories: {dialog.include_subdirectories()}")
        print(f"File extensions: {dialog.file_extensions()}")
        
        print("Bulk import dialog test completed successfully!")


if __name__ == "__main__":
    test_bulk_import_dialog()