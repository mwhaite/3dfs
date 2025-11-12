#!/usr/bin/env python3
"""Build a Flatpak bundle for 3dfs using flatpak-builder."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FLATPAK_DIR = PROJECT_ROOT / "packaging" / "flatpak"
DEFAULT_BUILD_DIR = PROJECT_ROOT / "build" / "flatpak"
DEFAULT_DIST_DIR = PROJECT_ROOT / "dist" / "flatpak"
DEFAULT_MANIFEST_TEMPLATE = FLATPAK_DIR / "io.open3dfs.ThreeDFS.json.in"


def _render_template(text: str, **placeholders: str) -> str:
    for key, value in placeholders.items():
        text = text.replace(key, value)
    return text


class PackagingError(RuntimeError):
    """Raised when the Flatpak packaging workflow cannot be completed."""


def _ensure_tool(tool: str) -> None:
    """Exit early if ``tool`` is not available on PATH."""

    if shutil.which(tool) is None:
        raise PackagingError(f"Required tool '{tool}' was not found on PATH.")


def _project_version() -> str:
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    return data["project"]["version"]


def _render_manifest(template: Path, version: str, output: Path) -> dict[str, str]:
    """Write the manifest with version placeholders resolved."""

    text = _render_template(
        template.read_text(),
        **{
            "@VERSION@": version,
            "@SOURCE_DIR@": str(PROJECT_ROOT.resolve()),
        },
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text)
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - template regression
        raise PackagingError(f"Rendered manifest is invalid JSON: {exc}") from exc
    return manifest


def _default_arch() -> str:
    result = subprocess.run(
        ["flatpak", "--default-arch"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PackagingError("Could not determine Flatpak default architecture.")
    return result.stdout.strip() or "x86_64"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a Flatpak bundle for 3dfs.")
    parser.add_argument(
        "--manifest-template",
        type=Path,
        default=DEFAULT_MANIFEST_TEMPLATE,
        help="Path to the manifest template (default: packaging/flatpak/io.open3dfs.ThreeDFS.json.in)",
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=DEFAULT_BUILD_DIR,
        help="Working directory for flatpak-builder (default: build/flatpak)",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Directory where the final bundle/repo should be written (default: dist/flatpak)",
    )
    parser.add_argument(
        "--arch",
        default=None,
        help="Target architecture (defaults to `flatpak --default-arch`).",
    )
    parser.add_argument(
        "--bundle",
        default="three-dfs.flatpak",
        help="Name of the resulting bundle file (default: three-dfs.flatpak)",
    )
    parser.add_argument(
        "--keep-build-dir",
        action="store_true",
        help="Do not delete the build directory after a successful run.",
    )
    parser.add_argument(
        "--user",
        action="store_true",
        help="Run flatpak-builder in user scope (avoids requiring elevated permissions).",
    )
    parser.add_argument(
        "--disable-rofiles-fuse",
        action="store_true",
        help="Pass --disable-rofiles-fuse to flatpak-builder for environments without FUSE support.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    _ensure_tool("flatpak-builder")
    _ensure_tool("flatpak")

    version = _project_version()
    build_dir = args.build_dir.resolve()
    dist_dir = args.dist_dir.resolve()
    repo_dir = dist_dir / "repo"
    manifest_path = build_dir / "io.open3dfs.ThreeDFS.json"

    manifest = _render_manifest(args.manifest_template, version, manifest_path)
    app_id = manifest.get("app-id", "io.open3dfs.ThreeDFS")

    arch = args.arch or _default_arch()

    # flatpak-builder expects the source tree to be available. It is executed from the project root.
    build_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)
    repo_dir.mkdir(parents=True, exist_ok=True)

    builder_cmd = [
        "flatpak-builder",
        "--force-clean",
        f"--default-branch={version}",
    ]
    if args.user:
        builder_cmd.append("--user")
    if args.disable_rofiles_fuse or os.environ.get("FLATPAK_DISABLE_ROFILES_FUSE") == "1":
        builder_cmd.append("--disable-rofiles-fuse")
    builder_cmd.extend(
        [
            "--repo",
            str(repo_dir),
            str(build_dir / "app"),
            str(manifest_path),
        ]
    )

    print("Running:", " ".join(builder_cmd))
    result = subprocess.run(builder_cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise PackagingError("flatpak-builder failed. See output above for details.")

    bundle_path = dist_dir / args.bundle
    bundle_cmd = [
        "flatpak",
        "build-bundle",
        str(repo_dir),
        str(bundle_path),
        app_id,
        version,
        "--runtime-repo=https://flathub.org/repo/flathub.flatpakrepo",
        f"--arch={arch}",
    ]

    print("Running:", " ".join(bundle_cmd))
    result = subprocess.run(bundle_cmd)
    if result.returncode != 0:
        raise PackagingError("flatpak build-bundle failed.")

    if not args.keep_build_dir:
        shutil.rmtree(build_dir, ignore_errors=True)

    print(f"Flatpak bundle created at {bundle_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    try:
        sys.exit(main())
    except PackagingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
