#!/usr/bin/env python3
"""Build a Debian package for 3dfs using dpkg-deb."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEB_DIR = PROJECT_ROOT / "packaging" / "deb"
DEFAULT_BUILD_DIR = PROJECT_ROOT / "build" / "deb"
DEFAULT_DIST_DIR = PROJECT_ROOT / "dist" / "deb"
DEFAULT_CONTROL_TEMPLATE = DEB_DIR / "control.in"


class PackagingError(RuntimeError):
    """Raised when Debian packaging fails."""


def _ensure_tool(tool: str) -> None:
    if shutil.which(tool) is None:
        raise PackagingError(f"Required tool '{tool}' not found on PATH.")


def _project_version() -> str:
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    return data["project"]["version"]


def _default_arch() -> str:
    result = subprocess.run(
        ["dpkg", "--print-architecture"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PackagingError("Unable to determine Debian architecture.")
    arch = result.stdout.strip()
    return arch or "amd64"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a Debian package for 3dfs.")
    parser.add_argument(
        "--control-template",
        type=Path,
        default=DEFAULT_CONTROL_TEMPLATE,
        help="Path to control file template (default: packaging/deb/control.in)",
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=DEFAULT_BUILD_DIR,
        help="Working directory (default: build/deb)",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Output directory for .deb (default: dist/deb)",
    )
    parser.add_argument(
        "--arch",
        default=None,
        help="Target Debian architecture (defaults to dpkg --print-architecture)",
    )
    parser.add_argument(
        "--package-name",
        default="three-dfs",
        help="Package name (default: three-dfs)",
    )
    parser.add_argument(
        "--keep-build-dir",
        action="store_true",
        help="Do not remove the build directory after completion.",
    )
    return parser.parse_args(argv)


def _render_control(template: Path, version: str, arch: str, destination: Path) -> None:
    text = template.read_text()
    text = text.replace("@VERSION@", version)
    text = text.replace("@ARCH@", arch)
    destination.write_text(text)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    _ensure_tool("dpkg-deb")
    _ensure_tool("pip")

    version = _project_version()
    arch = args.arch or _default_arch()

    build_dir = args.build_dir.resolve()
    dist_dir = args.dist_dir.resolve()
    staging = build_dir / "staging"
    debian_dir = staging / "DEBIAN"

    if staging.exists():
        shutil.rmtree(staging)

    # Prepare directories
    (staging / "usr" / "bin").mkdir(parents=True, exist_ok=True)
    (staging / "usr" / "share" / "applications").mkdir(parents=True, exist_ok=True)
    (staging / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps").mkdir(parents=True, exist_ok=True)
    debian_dir.mkdir(parents=True, exist_ok=True)

    # Install Python package into staging
    pip_cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--prefix",
        str(staging / "usr"),
        ".",
    ]
    print("Running:", " ".join(pip_cmd))
    result = subprocess.run(pip_cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise PackagingError("pip install failed")

    # Install launcher
    launcher_target = staging / "usr" / "bin" / "three-dfs"
    launcher_script = DEB_DIR / "three-dfs.sh"
    if not launcher_script.exists():
        raise PackagingError(f"Launcher script not found: {launcher_script}")
    shutil.copy2(launcher_script, launcher_target)
    launcher_target.chmod(0o755)

    # Desktop entry and icon
    desktop_file = DEB_DIR / "three-dfs.desktop"
    if not desktop_file.exists():
        raise PackagingError(f"Desktop file not found: {desktop_file}")
    shutil.copy2(desktop_file, staging / "usr" / "share" / "applications" / "three-dfs.desktop")
    
    icon_file = DEB_DIR / "three-dfs.png"
    if not icon_file.exists():
        raise PackagingError(f"Icon file not found: {icon_file}")
    shutil.copy2(icon_file, staging / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "three-dfs.png")

    control_path = debian_dir / "control"
    if not args.control_template.exists():
        raise PackagingError(f"Control template not found: {args.control_template}")
    _render_control(args.control_template, version, arch, control_path)

    dist_dir.mkdir(parents=True, exist_ok=True)
    package_name = f"{args.package_name}_{version}_{arch}.deb"
    output_deb = dist_dir / package_name

    build_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["dpkg-deb", "--build", str(staging), str(output_deb)]
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise PackagingError("dpkg-deb failed")

    if not output_deb.exists():
        raise PackagingError(f"Debian package was not created at expected path: {output_deb}")

    if not args.keep_build_dir:
        shutil.rmtree(build_dir, ignore_errors=True)

    print(f"Debian package created at {output_deb}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main())
    except PackagingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
