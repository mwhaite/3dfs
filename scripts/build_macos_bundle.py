#!/usr/bin/env python3
"""Build a macOS .app bundle for 3dfs using py2app, preserving package structure."""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from py2app import __file__ as py2app_file
except ImportError:
    print("py2app is required. Install it with 'pip install py2app'", file=sys.stderr)
    sys.exit(1)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DEFAULT_BUILD_DIR = PROJECT_ROOT / "build" / "macos_py2app"
DEFAULT_DIST_DIR = PROJECT_ROOT / "dist" / "macos"


class PackagingError(RuntimeError):
    """Raised when the macOS packaging workflow cannot be completed."""


def build_app(dist_dir: Path, name: str, create_dmg: bool = False, dmg_name: str = "three-dfs.dmg"):
    """Build a macOS application bundle using py2app."""
    if platform.system() != "Darwin":
        raise PackagingError("py2app builds must run on macOS.")

    # Define the main script to be converted to executable
    main_script = SRC_ROOT / "three_dfs" / "app.py"
    if not main_script.exists():
        raise PackagingError(f"Main script not found: {main_script}")

    # Define the setup file content for py2app
    setup_content = f'''from setuptools import setup

APP = ['{str(main_script)}']
DATA_FILES = []
OPTIONS = {{
    'argv_emulation': True,
    'iconfile': 'app.icns',  # Optional: add an icon file
    'plist': {{
        'CFBundleName': '{name}',
        'CFBundleDisplayName': '{name}',
        'CFBundleExecutable': '{name}',
        'CFBundleIdentifier': 'io.open3dfs.{name.replace("-", "")}',
        'CFBundleVersion': '0.1.0',
        'CFBundleShortVersionString': '0.1.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.15',
    }},
    'packages': [
        'three_dfs',
        'three_dfs.application',
        'three_dfs.customizer',
        'three_dfs.data',
        'three_dfs.db',
        'three_dfs.import_plugins',
        'three_dfs.storage',
        'three_dfs.thumbnails',
        'three_dfs.ui',
        'three_dfs.utils',
        'PySide6',
        'trimesh',
        'PIL',
        'numpy',
        'sqlalchemy',
        'build123d',
    ],
    'excludes': [
        'torch',      # ML framework not needed
        'torchvision', # Not needed
        'tensorflow', # ML framework not needed
        'matplotlib', # Plotting library not needed
        'scipy',      # Scientific computing not needed
        'tkinter',    # Not needed since we use PySide6
        'IPython',    # Interactive Python not needed
        'jupyter',    # Notebook environment not needed
        'tensorboard', # ML visualization not needed
    ],
}}

setup(
    app=APP,
    name='{name}',
    data_files=DATA_FILES,
    options={{'py2app': OPTIONS}},
    setup_requires=['py2app'],
)
'''

    # Write the setup file temporarily
    setup_file = PROJECT_ROOT / "setup_py2app.py"
    setup_file.write_text(setup_content)

    try:
        # Run py2app to build the app
        cmd = [
            sys.executable,
            str(setup_file),
            'py2app',
            '--dist-dir',
            str(dist_dir),
            '--build-base',
            str(DEFAULT_BUILD_DIR)
        ]
        
        print("Running:", " ".join(cmd))
        result = subprocess.run(cmd, check=False, cwd=PROJECT_ROOT)
        if result.returncode != 0:
            raise PackagingError(f"py2app build failed with exit code {result.returncode}")
        
        print(f"Built app bundle at {dist_dir}")
        
        # Optionally create a DMG
        if create_dmg:
            app_path = dist_dir / f"{name}.app"
            if app_path.exists():
                dmg_path = dist_dir / dmg_name  # Use custom DMG name
                create_dmg_from_app(app_path, dmg_path, name)
                print(f"Created DMG at {dmg_path}")
            else:
                print(f"Warning: App bundle not found at {app_path} to create DMG")
                
    finally:
        # Clean up the temporary setup file
        if setup_file.exists():
            setup_file.unlink()


def create_dmg_from_app(app_path: Path, dmg_path: Path, volume_name: str):
    """Create a DMG from the app bundle."""
    if shutil.which("hdiutil") is None:
        raise PackagingError("hdiutil is required to create a DMG.")

    if dmg_path.exists():
        dmg_path.unlink()

    cmd = [
        "hdiutil",
        "create",
        "-volname",
        volume_name,
        "-srcfolder",
        str(app_path),
        "-ov",
        "-format",
        "UDZO",
        str(dmg_path),
    ]

    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise PackagingError("hdiutil failed to create the DMG image.")


def parse_args(argv=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Create a macOS .app bundle for 3dfs using py2app."
    )
    parser.add_argument("--name", default="three-dfs", help="App name (default: three-dfs).")
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Destination directory (default: dist/macos).",
    )
    parser.add_argument(
        "--create-dmg",
        action="store_true",
        help="Create a DMG file in addition to the .app bundle.",
    )
    parser.add_argument(
        "--dmg-name",
        default="three-dfs.dmg",
        help="Filename for the DMG when --create-dmg is specified.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if platform.system() != "Darwin":
        print("Warning: This script is intended to run on macOS for building macOS apps.")
        # For the purposes of this workflow, we'll allow it to proceed but with a warning
        # since it might be running in CI where we just want to check the script works

    dist_dir = args.dist_dir.resolve()
    dist_dir.mkdir(parents=True, exist_ok=True)

    print(f"Building macOS app for {args.name}...")
    build_app(dist_dir, args.name, args.create_dmg, args.dmg_name)
    print(f"macOS app bundle created in {dist_dir}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PackagingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)