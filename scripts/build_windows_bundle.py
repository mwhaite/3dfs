#!/usr/bin/env python3
"""Build a Windows standalone executable for 3dfs without PyInstaller, preserving package structure."""

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DEFAULT_BUILD_DIR = PROJECT_ROOT / "build" / "windows_direct"
DEFAULT_DIST_DIR = PROJECT_ROOT / "dist" / "windows"


class PackagingError(RuntimeError):
    """Raised when the packaging workflow cannot be completed."""


def _create_windows_bundle(dist_dir: Path, name: str):
    """Create a Windows executable bundle without PyInstaller."""
    if platform.system() != "Windows":
        print("Warning: Building Windows bundle on non-Windows platform.")
    
    # Create distribution directory
    dist_dir = dist_dir / name
    dist_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy the source code
    app_dir = dist_dir / "three_dfs"
    shutil.copytree(SRC_ROOT / "three_dfs", app_dir)
    
    # Create a main script that can be executed with Python
    main_script = dist_dir / f"{name}.py"
    main_script.write_text("""#!/usr/bin/env python3
import sys
import os

# Add the directory containing this script to Python path
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

from three_dfs import app

if __name__ == "__main__":
    sys.exit(app.main())
""")
    
    # Create a batch file to run the Python script
    batch_file = dist_dir / f"{name}.bat"
    batch_file.write_text(f"""@echo off
python "{name}.py" %*
""")
    
    # If Python launcher is available, create a .py launcher as well
    launcher_script = dist_dir / f"{name}_launch.py"
    launcher_script.write_text(f"""#!/usr/bin/env python3
import sys
import os

# Get the directory where this script is located
script_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.join(script_dir, "three_dfs")

# Add the app directory to the Python path
sys.path.insert(0, script_dir)

from three_dfs import app

if __name__ == "__main__":
    sys.exit(app.main())
""")

    return dist_dir


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
        description="Create a Windows executable bundle for 3dfs without PyInstaller."
    )
    parser.add_argument("--name", default="three-dfs", help="Executable name (default: three-dfs).")
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Destination directory (default: dist/windows_direct).",
    )
    parser.add_argument(
        "--zip",
        action="store_true",
        help="Create a ZIP archive of the distribution output.",
    )
    parser.add_argument(
        "--allow-non-windows",
        action="store_true",
        help="Skip the Windows platform check (useful for CI environments).",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if platform.system() != "Windows" and not args.allow_non_windows:
        print("Warning: Building for Windows on a non-Windows system. This is OK for creating distribution files.")

    dist_root = args.dist_dir.resolve()
    dist_root.mkdir(parents=True, exist_ok=True)

    bundle_path = _create_windows_bundle(dist_root, args.name)
    print(f"Windows bundle created at {bundle_path}")

    if args.zip:
        archive = _zip_distribution(bundle_path, args.name)
        print(f"Created distribution archive at {archive}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PackagingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)