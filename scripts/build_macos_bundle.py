#!/usr/bin/env python3
"""Build a macOS .app bundle for 3dfs without PyInstaller, preserving package structure."""

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DEFAULT_BUILD_DIR = PROJECT_ROOT / "build" / "macos_direct"
DEFAULT_DIST_DIR = PROJECT_ROOT / "dist" / "macos"


class PackagingError(RuntimeError):
    """Raised when the macOS packaging workflow cannot be completed."""


def _create_app_bundle(dist_dir: Path, name: str, codesign_id: str = None):
    """Create a macOS app bundle without PyInstaller."""
    if platform.system().lower() != "darwin" and codesign_id:
        raise PackagingError("Code signing can only be performed on macOS.")
    
    # Create the .app bundle structure
    app_name = f"{name}.app"
    app_path = dist_dir / app_name
    contents_dir = app_path / "Contents"
    MacOS_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"
    python_dir = contents_dir / "Resources" / "Python"
    
    # Create required directories
    contents_dir.mkdir(parents=True)  # Create Contents directory first
    MacOS_dir.mkdir(parents=True)
    resources_dir.mkdir(parents=True)
    
    # Copy source code
    app_src_dir = python_dir / "three_dfs"
    shutil.copytree(SRC_ROOT / "three_dfs", app_src_dir)
    
    # Create a main executable script in MacOS directory
    main_executable = MacOS_dir / name
    main_executable.write_text(f"""#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
cd "$SCRIPT_DIR/../Resources/Python"
python3 -m three_dfs.app "$@"
""")
    main_executable.chmod(0o755)
    
    # Create Info.plist
    info_plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>{name}</string>
    <key>CFBundleIdentifier</key>
    <string>io.open3dfs.{name.replace('-', '')}</string>
    <key>CFBundleName</key>
    <string>{name}</string>
    <key>CFBundleVersion</key>
    <string>0.1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleSignature</key>
    <string>????</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSMinimumSystemVersion</key>
    <string>10.15</string>
</dict>
</plist>"""
    
    info_plist_path = contents_dir / "Info.plist"
    info_plist_path.write_text(info_plist_content)
    
    # If a codesign identity is provided, sign the app
    if codesign_id:
        codesign_cmd = [
            "codesign",
            "--deep",
            "--force",
            "--options",
            "runtime",
            "--sign",
            codesign_id,
            str(app_path),
        ]
        print("Running:", " ".join(codesign_cmd))
        result = subprocess.run(codesign_cmd)
        if result.returncode != 0:
            raise PackagingError("codesign failed.")
    
    return app_path


def _create_dmg(app_path: Path, output: Path, volume_name: str) -> None:
    """Create a DMG from the app bundle."""
    if shutil.which("hdiutil") is None:
        raise PackagingError("hdiutil is required to create a DMG.")

    if output.exists():
        output.unlink()

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
        str(output),
    ]

    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise PackagingError("hdiutil failed to create the DMG image.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a macOS .app bundle for 3dfs without PyInstaller.")
    parser.add_argument("--name", default="three-dfs", help="Name of the application bundle.")
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Destination directory (default: dist/macos_direct).",
    )
    parser.add_argument(
        "--create-dmg",
        action="store_true",
        help="Generate a compressed DMG alongside the .app bundle.",
    )
    parser.add_argument(
        "--dmg-name",
        default="three-dfs.dmg",
        help="Filename for the DMG when --create-dmg is specified.",
    )
    parser.add_argument(
        "--codesign-id",
        default=None,
        help="Codesign identity to apply to the .app bundle (optional).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    dist_dir = args.dist_dir.resolve()
    dist_dir.mkdir(parents=True, exist_ok=True)

    app_path = _create_app_bundle(dist_dir, args.name, args.codesign_id)
    print(f"macOS app bundle created at {app_path}")

    if args.create_dmg:
        dmg_path = dist_dir / args.dmg_name
        _create_dmg(app_path, dmg_path, volume_name=args.name)
        print(f"DMG created at {dmg_path}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PackagingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)