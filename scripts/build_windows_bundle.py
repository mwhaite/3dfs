#!/usr/bin/env python3
"""Build a Windows executable for 3dfs using cx_Freeze, preserving package structure."""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from cx_Freeze import setup, Executable
    from cx_Freeze.common import rebuild_code_object
except ImportError:
    print("cx_Freeze is required. Install it with 'pip install cx_freeze'", file=sys.stderr)
    sys.exit(1)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DEFAULT_BUILD_DIR = PROJECT_ROOT / "build" / "windows_exe"
DEFAULT_DIST_DIR = PROJECT_ROOT / "dist" / "windows"


class PackagingError(RuntimeError):
    """Raised when the packaging workflow cannot be completed."""


def build_executable(dist_dir: Path, name: str):
    """Build a Windows executable using cx_Freeze."""
    if platform.system() != "Windows":
        print("Warning: Building for Windows on a non-Windows system. This is OK for creating distribution files.")

    # Define the main script to be converted to executable
    main_script = SRC_ROOT / "three_dfs" / "app.py"
    if not main_script.exists():
        raise PackagingError(f"Main script not found: {main_script}")

    # Define the executable
    build_exe_options = {
        "packages": [
            "three_dfs",
        ],
        "includes": [
            "three_dfs.app",
            "three_dfs.config",
            "three_dfs.paths",
        ],
        "excludes": [
            "torch",      # ML framework not needed
            "torchvision", # Not needed
            "tensorflow", # ML framework not needed
            "matplotlib", # Plotting library not needed
            "scipy",      # Scientific computing not needed
            "tkinter",    # Not needed since we use PySide6
            "IPython",    # Interactive Python not needed
            "jupyter",    # Notebook environment not needed
            "tensorboard", # ML visualization not needed
            "IPython",
            "jupyter_client",
            "jupyter_core",
            "notebook",
            "nbconvert",
            "nbformat",
        ],
        "include_files": [],
        "build_exe": str(dist_dir / name),
        "optimize": 2,
    }

    # Create the executable specification
    executable = Executable(
        script=str(main_script),
        target_name=f"{name}.exe",
        base="Win32GUI" if os.name == "nt" else None,  # Use GUI subsystem on Windows
    )

    # Run cx_Freeze setup
    try:
        setup(
            name=name,
            version="0.1.0",
            description="3dfs application",
            options={"build_exe": build_exe_options},
            executables=[executable]
        )
    except SystemExit as e:
        # cx_Freeze calls sys.exit(0) when successful, so we catch that
        if e.code != 0:
            raise PackagingError(f"cx_Freeze build failed with exit code {e.code}")


def _zip_distribution(dist_dir: Path, name: str) -> Path:
    """Create a ZIP archive of the built distribution directory."""
    if not dist_dir.exists():
        raise PackagingError(
            f"Distribution folder {dist_dir} does not exist."
        )
    archive_path = dist_dir.parent / f"{name}.zip"
    if archive_path.exists():
        archive_path.unlink()
    return Path(shutil.make_archive(str(archive_path.with_suffix("")), "zip", dist_dir))


def parse_args(argv=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Create a Windows executable for 3dfs using cx_Freeze."
    )
    parser.add_argument("--name", default="three-dfs", help="Executable name (default: three-dfs).")
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Destination directory (default: dist/windows).",
    )
    parser.add_argument(
        "--zip",
        action="store_true",
        help="Create a ZIP archive of the distribution output.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    dist_dir = args.dist_dir.resolve()
    dist_dir.mkdir(parents=True, exist_ok=True)

    print(f"Building Windows executable for {args.name}...")
    build_executable(dist_dir, args.name)
    print(f"Windows executable created in {dist_dir / args.name}")

    if args.zip:
        archive = _zip_distribution(dist_dir / args.name, args.name)
        print(f"Created distribution archive at {archive}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PackagingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)