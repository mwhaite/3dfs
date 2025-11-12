#!/usr/bin/env python3
"""Build a Windows standalone executable for 3dfs using PyInstaller.

This script wraps the manual packaging steps outlined in the project docs. Run it
on a Windows machine where the project (and PyInstaller) are installed to produce
a redistributable directory or single-file executable. Optionally, copy an
external OpenSCAD binary into the bundled output so users can run
OpenSCAD-dependent customizer flows without a separate installation.
"""
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
DEFAULT_BUILD_DIR = PROJECT_ROOT / "build" / "windows"
DEFAULT_DIST_DIR = PROJECT_ROOT / "dist" / "windows"
DEFAULT_SPEC_DIR = DEFAULT_BUILD_DIR


class PackagingError(RuntimeError):
    """Raised when the packaging workflow cannot be completed."""


def _ensure_pyinstaller() -> None:
    """Exit early if PyInstaller is not available."""
    try:
        import PyInstaller  # noqa: F401  (imported for side effect)
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
    one_file: bool,
    extra_collect_all: Iterable[str],
    extra_hidden_imports: Iterable[str],
    icon: Path | None,
) -> list[str]:
    """Construct the PyInstaller invocation."""
    command: list[str] = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        name,
        "--noconsole",
        "--clean",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(spec_dir),
    ]

    if one_file:
        command.append("--onefile")

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

    hidden_imports: Sequence[str] = (
        "OpenGL",
    )
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
    """Invoke PyInstaller and surface a helpful error on failure."""
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise PackagingError(
            "PyInstaller exited with a non-zero status. Inspect the log above for details."
        )


def _bundle_openscad(executable: Path, destination_root: Path) -> Path:
    """Copy an external OpenSCAD executable into the bundled output."""
    if not executable.exists():
        raise PackagingError(f"OpenSCAD executable not found: {executable}")
    bundle_dir = destination_root / "openscad"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    destination = bundle_dir / executable.name
    shutil.copy2(executable, destination)
    return destination


def _zip_distribution(dist_dir: Path, name: str) -> Path:
    """Create a ZIP archive of the built distribution directory."""
    archive_root = dist_dir / name
    if not archive_root.exists():
        raise PackagingError(
            f"Expected distribution folder {archive_root} was not produced by PyInstaller."
        )
    archive_path = dist_dir / f"{name}.zip"
    if archive_path.exists():
        archive_path.unlink()
    return Path(shutil.make_archive(str(archive_path.with_suffix("")), "zip", dist_dir, name))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Freeze the three_dfs.app entry point into a Windows executable using PyInstaller. "
            "Run the script from a Windows virtual environment where project dependencies are installed."
        )
    )
    parser.add_argument("--name", default="three-dfs", help="Executable name (default: three-dfs).")
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Destination directory for PyInstaller output (default: dist/windows).",
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=DEFAULT_BUILD_DIR,
        help="PyInstaller working directory (default: build/windows).",
    )
    parser.add_argument(
        "--spec-dir",
        type=Path,
        default=DEFAULT_SPEC_DIR,
        help="Directory where the generated spec file is written (default: build/windows).",
    )
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Emit a single-file executable instead of a folder distribution.",
    )
    parser.add_argument(
        "--collect-all",
        dest="extra_collect_all",
        action="append",
        default=[],
        help="Additional packages to pass to PyInstaller --collect-all.",
    )
    parser.add_argument(
        "--hidden-import",
        dest="extra_hidden_imports",
        action="append",
        default=[],
        help="Additional hidden imports to expose to PyInstaller.",
    )
    parser.add_argument(
        "--icon",
        type=Path,
        help="Optional path to a .ico file used as the application icon.",
    )
    parser.add_argument(
        "--bundle-openscad",
        type=Path,
        help="Copy the provided OpenSCAD executable into the distribution folder.",
    )
    parser.add_argument(
        "--zip",
        action="store_true",
        help="Create a ZIP archive of the distribution output after a successful build.",
    )
    parser.add_argument(
        "--allow-non-windows",
        action="store_true",
        help="Skip the Windows platform check (useful for CI environments).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if platform.system() != "Windows" and not args.allow_non_windows:
        raise PackagingError(
            "Windows packaging must run on Windows. Rerun with --allow-non-windows to override."
        )

    args.dist_dir.mkdir(parents=True, exist_ok=True)
    args.build_dir.mkdir(parents=True, exist_ok=True)
    args.spec_dir.mkdir(parents=True, exist_ok=True)

    _ensure_pyinstaller()

    command = _build_command(
        name=args.name,
        dist_dir=args.dist_dir,
        build_dir=args.build_dir,
        spec_dir=args.spec_dir,
        one_file=args.onefile,
        extra_collect_all=args.extra_collect_all,
        extra_hidden_imports=args.extra_hidden_imports,
        icon=args.icon,
    )

    _run_pyinstaller(command)

    dist_root = (args.dist_dir / args.name) if not args.onefile else args.dist_dir
    if args.bundle_openscad is not None:
        bundled_path = _bundle_openscad(args.bundle_openscad, dist_root)
        print(f"Copied OpenSCAD executable to {bundled_path}")

    if args.zip and not args.onefile:
        archive = _zip_distribution(args.dist_dir, args.name)
        print(f"Created distribution archive at {archive}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PackagingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
