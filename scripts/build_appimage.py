#!/usr/bin/env python3
"""Build a Linux AppImage for 3dfs using PyInstaller output as the payload."""

from __future__ import annotations

import argparse
import base64
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

import tomllib

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
ENTRY_SCRIPT = SRC_ROOT / "three_dfs" / "app.py"
APPIMAGE_TEMPLATE_DIR = PROJECT_ROOT / "appimage"
DEFAULT_BUILD_DIR = PROJECT_ROOT / "build" / "linux"
DEFAULT_DIST_DIR = PROJECT_ROOT / "dist" / "linux"
DEFAULT_APPDIR = DEFAULT_BUILD_DIR / "AppDir"


class PackagingError(RuntimeError):
    """Raised when the AppImage packaging workflow fails."""


def _ensure_pyinstaller() -> None:
    """Exit early if PyInstaller is not available."""
    try:
        import PyInstaller  # noqa: F401 - imported for the availability check
    except ModuleNotFoundError as exc:  # pragma: no cover - defensive branch
        raise PackagingError(
            "PyInstaller is required. Install it with 'python -m pip install pyinstaller'."
        ) from exc


def _project_version() -> str:
    """Read the project version from pyproject.toml."""
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    return data["project"]["version"]


def _build_command(
    *,
    name: str,
    dist_dir: Path,
    build_dir: Path,
    spec_dir: Path,
    extra_collect_all: Iterable[str],
    extra_hidden_imports: Iterable[str],
) -> list[str]:
    """Construct the PyInstaller command used for the AppImage payload."""
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

    collect_targets: Sequence[str] = (
        "shiboken6",
        "PySide6",
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
        "OpenGL.GL",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtOpenGL",
    )
    for module in hidden_imports:
        command.extend(("--hidden-import", module))
    for module in extra_hidden_imports:
        command.extend(("--hidden-import", module))

    if not ENTRY_SCRIPT.exists():
        raise PackagingError(f"Entry script not found: {ENTRY_SCRIPT}")
    
    # Add metadata collection for better AppImage compatibility
    metadata_targets: Sequence[str] = (
        "PySide6",
        "shiboken6",
    )
    for target in metadata_targets:
        command.extend(("--copy-metadata", target))
    
    command.append(str(ENTRY_SCRIPT))
    return command


def _run_pyinstaller(command: Sequence[str]) -> None:
    """Invoke PyInstaller and raise a friendly error on failure."""
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise PackagingError(
            "PyInstaller exited with a non-zero status. Inspect the log above for details."
        )


def _copy_template(appdir: Path, version: str) -> None:
    """Seed the AppDir with launcher scripts, desktop entries, and icons."""
    if not APPIMAGE_TEMPLATE_DIR.exists():
        raise PackagingError(
            f"AppImage template directory not found: {APPIMAGE_TEMPLATE_DIR}"  # pragma: no cover
        )

    appdir.mkdir(parents=True, exist_ok=True)
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
    else:  # pragma: no cover - defensive branch for missing asset
        raise PackagingError(
            f"Missing icon payload: {icon_b64_path}. Did you delete the AppImage template asset?"
        )

    # Store a copy of the desktop file at the AppDir root per AppImage conventions.
    (appdir / "three-dfs.desktop").write_text(desktop_contents)


def _write_launcher(appdir: Path) -> Path:
    """Create the wrapper executable that dispatches to the PyInstaller binary."""
    launcher_dir = appdir / "usr" / "bin"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    launcher_path = launcher_dir / "three-dfs"
    launcher_path.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        "\n"
        "# Get the directory containing this script\n"
        'HERE="$(dirname "$(readlink -f "$0")"\n'
        'APPDIR="$HERE/../.."\n'
        'PAYLOAD_DIR="$APPDIR/usr/lib/three-dfs"\n'
        'EXECUTABLE="$PAYLOAD_DIR/three-dfs"\n'
        "\n"
        "# Ensure library paths include bundled libraries\n"
        'export LD_LIBRARY_PATH="$APPDIR/usr/lib:$APPDIR/usr/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
        "\n"
        "# Enable debug output if APPIMAGE_DEBUG is set\n"
        "if [ -n \"$APPIMAGE_DEBUG\" ]; then\n"
        "  set -x\n"
        '  echo "[launcher] HERE=$HERE" >&2\n'
        '  echo "[launcher] APPDIR=$APPDIR" >&2\n'
        '  echo "[launcher] EXECUTABLE=$EXECUTABLE" >&2\n'
        '  echo "[launcher] LD_LIBRARY_PATH=$LD_LIBRARY_PATH" >&2\n'
        "fi\n"
        "\n"
        "# Verify the executable exists\n"
        'if [ ! -f "$EXECUTABLE" ]; then\n'
        '  echo "[launcher] Error: executable not found: $EXECUTABLE" >&2\n'
        '  echo "[launcher] Payload directory contents:" >&2\n'
        '  ls -la "$PAYLOAD_DIR" >&2 2>/dev/null || echo "[launcher] (directory not accessible)" >&2\n'
        "  exit 1\n"
        "fi\n"
        "\n"
        "# Execute the frozen application\n"
        'exec "$EXECUTABLE" "$@"\n'
    )
    launcher_path.chmod(0o755)
    return launcher_path


def _stage_pyinstaller_payload(pyinstaller_dir: Path, appdir: Path) -> Path:
    """Copy the PyInstaller one-folder build into the AppDir."""
    if not pyinstaller_dir.exists():
        raise PackagingError(
            f"Expected PyInstaller output directory was not found: {pyinstaller_dir}"
        )
    target_dir = appdir / "usr" / "lib" / "three-dfs"
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(pyinstaller_dir, target_dir)
    return target_dir


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
    result = subprocess.run(
        [str(appimagetool), str(appdir), str(output_path)], check=False
    )
    if result.returncode != 0:
        raise PackagingError("appimagetool exited with a non-zero status.")
    return output_path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Freeze the three_dfs.app entry point using PyInstaller and wrap the result into an AppImage. "
            "Run the script from a Linux environment where project dependencies are installed."
        )
    )
    parser.add_argument(
        "--name", default="three-dfs", help="Application name (default: three-dfs)."
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Destination directory for PyInstaller output and the AppImage artifact.",
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=DEFAULT_BUILD_DIR,
        help="Working directory for PyInstaller's intermediate files.",
    )
    parser.add_argument(
        "--spec-dir",
        type=Path,
        default=DEFAULT_BUILD_DIR,
        help="Directory where the generated PyInstaller spec file should be written.",
    )
    parser.add_argument(
        "--appdir",
        type=Path,
        default=DEFAULT_APPDIR,
        help="Location where the temporary AppDir should be staged.",
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
        "--appimagetool",
        type=Path,
        help="Optional path to the appimagetool executable. When provided the script produces the final AppImage.",
    )
    parser.add_argument(
        "--allow-non-linux",
        action="store_true",
        help="Skip the Linux platform check (useful for CI environments).",
    )
    parser.add_argument(
        "--skip-appimagetool",
        action="store_true",
        help="Prepare the AppDir but do not invoke appimagetool even if available.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the AppImage build workflow."""
    args = parse_args(argv)

    if platform.system() != "Linux" and not args.allow_non_linux:
        raise PackagingError(
            "AppImage builds must run on Linux. Pass --allow-non-linux to override."
        )

    _ensure_pyinstaller()
    version = _project_version()

    args.dist_dir.mkdir(parents=True, exist_ok=True)
    args.build_dir.mkdir(parents=True, exist_ok=True)

    command = _build_command(
        name=args.name,
        dist_dir=args.dist_dir,
        build_dir=args.build_dir,
        spec_dir=args.spec_dir,
        extra_collect_all=args.extra_collect_all,
        extra_hidden_imports=args.extra_hidden_imports,
    )
    _run_pyinstaller(command)

    pyinstaller_output = args.dist_dir / args.name
    if not pyinstaller_output.exists():
        raise PackagingError(
            f"PyInstaller did not produce the expected output folder: {pyinstaller_output}"
        )

    if args.appdir.exists():
        shutil.rmtree(args.appdir)
    _copy_template(args.appdir, version)
    _write_launcher(args.appdir)
    _stage_pyinstaller_payload(pyinstaller_output, args.appdir)

    if args.skip_appimagetool:
        print(
            f"AppDir staged at {args.appdir}. appimagetool invocation skipped by request."
        )
        return 0

    if args.appimagetool is None:
        print(
            "AppDir staged successfully. Provide --appimagetool /path/to/appimagetool to produce the final AppImage."
        )
        return 0

    output_path = _run_appimagetool(
        args.appdir, args.appimagetool, args.dist_dir, args.name, version
    )
    if not output_path.exists():
        raise PackagingError(f"AppImage was not created at expected path: {output_path}")
    print(f"AppImage written to {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PackagingError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
