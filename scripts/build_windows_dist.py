#!/usr/bin/env python3
"""
Build script for Windows distribution.
Wraps cx_Freeze and creates a zip archive of the output.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Constants
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = PROJECT_ROOT / "dist" / "windows"
BUILD_DIR = PROJECT_ROOT / "build"


def clean():
    """Clean build and dist directories."""
    if DIST_DIR.exists():
        print(f"Cleaning {DIST_DIR}...")
        shutil.rmtree(DIST_DIR)
    
    # We might not want to blow away the whole build dir if other platforms use it,
    # but for Windows CI it's fine. On local it might be annoying. 
    # Let's just rely on cx_Freeze to update or overwrite.


def build_exe():
    """Run cx_Freeze build."""
    print("Running cx_Freeze...")
    cmd = [sys.executable, "-m", "cx_Freeze", "build"]
    subprocess.check_call(cmd, cwd=PROJECT_ROOT)


def package_zip():
    """Zip the build output."""
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    
    # Find the exe build directory
    # cx_Freeze defaults to build/exe.<platform>-<python_version>
    # e.g., build/exe.win-amd64-3.11
    
    exe_dirs = list(BUILD_DIR.glob("exe.*"))
    if not exe_dirs:
        print("Error: No build output found in build/", file=sys.stderr)
        sys.exit(1)
        
    # Pick the most recently modified one if multiple? Or just the first one?
    # In a clean CI env, there should be only one.
    src_dir = exe_dirs[0]
    print(f"Found build output: {src_dir}")
    
    zip_name = DIST_DIR / "three-dfs-windows"
    print(f"Creating zip archive: {zip_name}.zip")
    
    shutil.make_archive(
        base_name=str(zip_name),
        format="zip",
        root_dir=src_dir.parent,
        base_dir=src_dir.name
    )
    print("Packaging complete.")


def main():
    clean()
    build_exe()
    package_zip()


if __name__ == "__main__":
    main()
