#!/usr/bin/env python3
"""Build a macOS .app bundle (and optional DMG) for 3dfs using PyInstaller."""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRY_SCRIPT = PROJECT_ROOT / "src" / "three_dfs" / "app.py"
DEFAULT_BUILD_DIR = PROJECT_ROOT / "build" / "macos"
DEFAULT_DIST_DIR = PROJECT_ROOT / "dist" / "macos"
DEFAULT_SPEC_DIR = DEFAULT_BUILD_DIR


class PackagingError(RuntimeError):
    """Raised when the macOS packaging workflow cannot be completed."""


def _ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401  (imported for availability check)
    except ModuleNotFoundError as exc:  # pragma: no cover - defensive branch
        raise PackagingError(
            "PyInstaller is required. Install it with 'python -m pip install pyinstaller'."
        ) from exc


def _build_command(
    *,
    name: str,
    dist_dir: Path,
    build_dir: Path,
    spec_dir: Path,
    extra_collect_all: Iterable[str],
    extra_hidden_imports: Iterable[str],
    icon: Path | None,
) -> list[str]:
    command: list[str] = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        name,
        "--windowed",
        "--noconsole",
        "--clean",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(spec_dir),
    ]

    collect_targets: Sequence[str] = (
        "shiboken6",
        "trimesh",
        "build123d",
        "PIL",
        "numpy",
        "sqlalchemy",
    )
    for target in collect_targets:
        command.extend(("--collect-all", target))
    for target in extra_collect_all:
        command.extend(("--collect-all", target))

    hidden_imports: Sequence[str] = ("OpenGL",)
    for module in hidden_imports:
        command.extend(("--hidden-import", module))
    for module in extra_hidden_imports:
        command.extend(("--hidden-import", module))

    if icon is not None:
        command.extend(("--icon", str(icon)))

    if not ENTRY_SCRIPT.exists():
        raise PackagingError(f"Entry script not found: {ENTRY_SCRIPT}")
    command.append(str(ENTRY_SCRIPT))
    return command


def _run_pyinstaller(command: Sequence[str]) -> None:
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise PackagingError("PyInstaller exited with a non-zero status.")


def _create_dmg(bundle_root: Path, output: Path, volume_name: str) -> None:
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
        str(bundle_root),
        "-ov",
        "-format",
        "UDZO",
        str(output),
    ]

    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise PackagingError("hdiutil failed to create the DMG image.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a macOS .app bundle for 3dfs.")
    parser.add_argument("--name", default="three-dfs", help="Name of the application bundle.")
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Destination directory for PyInstaller output (default: dist/macos).",
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=DEFAULT_BUILD_DIR,
        help="PyInstaller working directory (default: build/macos).",
    )
    parser.add_argument(
        "--spec-dir",
        type=Path,
        default=DEFAULT_SPEC_DIR,
        help="Directory where the generated spec file is written (default: build/macos).",
    )
    parser.add_argument(
        "--extra-collect",
        action="append",
        default=[],
        help="Additional packages to pass to --collect-all.",
    )
    parser.add_argument(
        "--extra-hidden-import",
        action="append",
        default=[],
        help="Additional modules to add as hidden imports.",
    )
    parser.add_argument(
        "--icon",
        type=Path,
        default=None,
        help="Optional .icns icon file to embed in the app bundle.",
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
    if platform.system().lower() != "darwin":
        raise PackagingError("This script must be run on macOS.")

    args = parse_args(argv)
    _ensure_pyinstaller()

    dist_dir = args.dist_dir.resolve()
    build_dir = args.build_dir.resolve()
    spec_dir = args.spec_dir.resolve()

    for path in (dist_dir, build_dir, spec_dir):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)

    command = _build_command(
        name=args.name,
        dist_dir=dist_dir,
        build_dir=build_dir,
        spec_dir=spec_dir,
        extra_collect_all=args.extra_collect,
        extra_hidden_imports=args.extra_hidden_import,
        icon=args.icon,
    )

    print("Running:", " ".join(command))
    _run_pyinstaller(command)

    app_path = dist_dir / f"{args.name}.app"
    if not app_path.exists():
        raise PackagingError(f"Expected bundle {app_path} was not produced by PyInstaller.")

    if args.codesign_id:
        codesign_cmd = [
            "codesign",
            "--deep",
            "--force",
            "--options",
            "runtime",
            "--sign",
            args.codesign_id,
            str(app_path),
        ]
        print("Running:", " ".join(codesign_cmd))
        result = subprocess.run(codesign_cmd)
        if result.returncode != 0:
            raise PackagingError("codesign failed.")

    if args.create_dmg:
        dmG_path = dist_dir / args.dmg_name
        _create_dmg(app_path, dmG_path, volume_name=args.name)
        print(f"DMG created at {dmG_path}")

    print(f"macOS app bundle available at {app_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    try:
        sys.exit(main())
    except PackagingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
