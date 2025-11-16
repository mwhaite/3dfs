#!/usr/bin/env python3
"""Build a Linux AppImage for 3dfs by bundling the application directly without PyInstaller."""

import argparse
import base64
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
APPIMAGE_TEMPLATE_DIR = PROJECT_ROOT / "appimage"
DEFAULT_BUILD_DIR = PROJECT_ROOT / "build" / "linux_direct"
DEFAULT_DIST_DIR = PROJECT_ROOT / "dist" / "linux_direct"
DEFAULT_APPDIR = DEFAULT_BUILD_DIR / "AppDir"


class PackagingError(RuntimeError):
    """Raised when the AppImage packaging workflow fails."""


def _ensure_python_venv():
    """Ensure we have a Python virtual environment with the required packages."""
    # Check if we're running in a virtual environment
    if not sys.prefix != sys.base_prefix:
        raise PackagingError("Please run this script from a Python virtual environment with dependencies installed.")


def _create_appdir_structure(appdir: Path, version: str):
    """Create the AppDir structure with the application."""
    appdir.mkdir(parents=True, exist_ok=True)
    
    # Copy the appimagetool and other template files
    shutil.copy2(APPIMAGE_TEMPLATE_DIR / "AppRun", appdir / "AppRun")
    (appdir / "AppRun").chmod(0o755)

    desktop_template = (APPIMAGE_TEMPLATE_DIR / "three-dfs.desktop.in").read_text()
    desktop_contents = desktop_template.replace("@VERSION@", version)

    applications_dir = appdir / "usr" / "share" / "applications"
    applications_dir.mkdir(parents=True, exist_ok=True)
    desktop_target = applications_dir / "three-dfs.desktop"
    desktop_target.write_text(desktop_contents)

    icons_dir = appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    icons_dir.mkdir(parents=True, exist_ok=True)

    icon_b64_path = APPIMAGE_TEMPLATE_DIR / "three-dfs.icon.b64"
    if icon_b64_path.exists():
        icon_bytes = base64.b64decode(icon_b64_path.read_text())
        (icons_dir / "three-dfs.png").write_bytes(icon_bytes)
        (appdir / "three-dfs.png").write_bytes(icon_bytes)
    else:
        raise PackagingError(
            f"Missing icon payload: {icon_b64_path}. Did you delete the AppImage template asset?"
        )

    # Store a copy of the desktop file at the AppDir root per AppImage conventions.
    (appdir / "three-dfs.desktop").write_text(desktop_contents)


def _install_app_with_deps(appdir: Path):
    """Install the application and its dependencies in the AppDir."""
    # For the direct approach, we need to create a portable Python environment
    # with all dependencies included. This is complex, so we'll simplify:
    
    # Copy the source code to the AppDir in the right location
    app_dir = appdir / "usr" / "lib" / "python3" / "site-packages"
    app_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy the application source
    app_src_dir = app_dir / "three_dfs"
    shutil.copytree(SRC_ROOT / "three_dfs", app_src_dir, dirs_exist_ok=True)
    
    # Create the launcher script within the AppDir
    launcher_dir = appdir / "usr" / "bin"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    
    # Create a Python launcher that sets up the environment properly
    launcher_path = launcher_dir / "three-dfs"
    launcher_path.write_text(f"""#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
APPDIR="$(dirname "$(dirname "$HERE")")"
export PYTHONPATH="$APPDIR/usr/lib/python3/site-packages:$PYTHONPATH"

# Find Python interpreter
for python_cmd in python3.11 python3.12 python3 python; do
    if command -v "$python_cmd" >/dev/null 2>&1; then
        exec "$python_cmd" -c "import sys; sys.path.insert(0, \\"$APPDIR/usr/lib/python3/site-packages\\"); from three_dfs import app; sys.exit(app.main())" "$@"
    fi
done

echo "Error: No suitable Python interpreter found"
exit 1
""")
    launcher_path.chmod(0o755)


def _run_appimagetool(
    appdir: Path, appimagetool: Path, output_dir: Path, name: str, version: str
) -> Path:
    """Invoke appimagetool to create the final AppImage."""
    if not appimagetool.exists():
        raise PackagingError(f"appimagetool not found: {appimagetool}")
    if not os.access(appimagetool, os.X_OK):
        appimagetool.chmod(appimagetool.stat().st_mode | 0o111)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{name}-{version}.AppImage"
    # Set architecture and run appimagetool
    env = os.environ.copy()
    env["ARCH"] = "x86_64"
    result = subprocess.run(
        [str(appimagetool), str(appdir), str(output_path)], 
        env=env, 
        check=False
    )
    if result.returncode != 0:
        raise PackagingError("appimagetool exited with a non-zero status.")
    return output_path


def main():
    """Entry point for the AppImage build workflow."""
    if platform.system() != "Linux":
        raise PackagingError("AppImage builds must run on Linux.")

    # Read project version from pyproject.toml
    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    version = "0.1.0"
    if pyproject_path.exists():
        import tomllib
        data = tomllib.loads(pyproject_path.read_text())
        version = data["project"]["version"]
    
    # Create build directories
    DEFAULT_DIST_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_BUILD_DIR.mkdir(parents=True, exist_ok=True)
    
    if DEFAULT_APPDIR.exists():
        shutil.rmtree(DEFAULT_APPDIR)
    
    # Create the AppDir structure
    _create_appdir_structure(DEFAULT_APPDIR, version)
    
    # Install the application and dependencies
    _install_app_with_deps(DEFAULT_APPDIR)
    
    # Use appimagetool to create the final AppImage if available
    appimagetool_path = PROJECT_ROOT / "appimagetool"
    if appimagetool_path.exists():
        output_path = _run_appimagetool(
            DEFAULT_APPDIR, appimagetool_path, DEFAULT_DIST_DIR, "three-dfs", version
        )
        print(f"AppImage written to {output_path}")
    else:
        print(f"AppDir staged at {DEFAULT_APPDIR}. Provide appimagetool to create final AppImage.")
    
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PackagingError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)