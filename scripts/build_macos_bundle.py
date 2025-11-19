#!/usr/bin/env python3
"""Build the macOS application bundle and DMG via py2app."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - fallback for Python <=3.10
    import tomli as tomllib  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
ENTRY_SCRIPT = ROOT / "scripts/three_dfs_entry.py"
PYPROJECT = ROOT / "pyproject.toml"
DEFAULT_BUILD_DIR = ROOT / "build" / "macos"


def log(message: str) -> None:
    """Display a prefixed status message."""
    print(f"[macos] {message}")


def run_command(command: list[str], *, cwd: Path | None = None) -> None:
    """Run a subprocess with logging."""
    pretty = " ".join(shlex.quote(part) for part in command)
    log(f"$ {pretty}")
    subprocess.run(command, check=True, cwd=str(cwd or ROOT))


def ensure_macos() -> None:
    if sys.platform != "darwin":  # pragma: no cover - GitHub runner enforces this
        raise SystemExit("macOS packaging must run on macOS.")


def ensure_py2app_installed() -> None:
    try:
        import py2app  # noqa: F401  # pylint: disable=import-outside-toplevel, unused-import
    except ImportError as exc:  # pragma: no cover - depends on runner state
        raise SystemExit("py2app is missing. Install it via packaging/macos/requirements.txt.") from exc


def ensure_pyobjc_installed() -> None:
    try:
        import Cocoa  # type: ignore  # noqa: F401  # pylint: disable=import-outside-toplevel, unused-import
        import Quartz  # type: ignore  # noqa: F401  # pylint: disable=import-outside-toplevel, unused-import
        import LaunchServices  # type: ignore  # noqa: F401  # pylint: disable=import-outside-toplevel, unused-import
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "PyObjC frameworks missing. Install pyobjc-core, pyobjc-framework-Cocoa, "
            "pyobjc-framework-Quartz, and pyobjc-framework-LaunchServices"
        ) from exc


def read_pyproject() -> dict[str, Any]:
    if not PYPROJECT.exists():
        raise SystemExit("pyproject.toml not found. Run from the repository root.")
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def normalize_options(raw: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for key, value in raw.items():
        if key in {"dist-dir", "plist"}:
            continue
        options[key.replace("-", "_")] = value
    return options


def prepare_configuration() -> tuple[str, str, dict[str, Any], dict[str, Any], Path, Path, str]:
    pyproject = read_pyproject()
    project = pyproject.get("project", {})
    project_name = project.get("name", "three-dfs")
    project_version = project.get("version", "0.0.0")

    tool_section = pyproject.get("tool", {})
    py2app_section = dict(tool_section.get("py2app", {}))
    plist_cfg = dict(py2app_section.pop("plist", {}))
    py2app_options = normalize_options(py2app_section)

    bundle_name = plist_cfg.get("CFBundleName") or project_name

    dist_dir = Path(py2app_section.get("dist-dir", "dist/macos"))
    if not dist_dir.is_absolute():
        dist_dir = ROOT / dist_dir
    build_dir = DEFAULT_BUILD_DIR

    if plist_cfg:
        plist_cfg.setdefault("CFBundleName", bundle_name)
        plist_cfg.setdefault("CFBundleDisplayName", bundle_name)
        plist_cfg.setdefault("CFBundleIdentifier", f"io.open3dfs.{bundle_name.replace('-', '')}")
        plist_cfg["CFBundleVersion"] = project_version
        plist_cfg["CFBundleShortVersionString"] = project_version
        py2app_options["plist"] = plist_cfg

    return project_name, project_version, py2app_options, plist_cfg, dist_dir, build_dir, bundle_name


def clean_previous_outputs(dist_dir: Path, build_dir: Path, bundle_name: str, dmg_name: str) -> None:
    shutil.rmtree(build_dir, ignore_errors=True)
    shutil.rmtree(dist_dir / f"{bundle_name}.app", ignore_errors=True)
    target_dmg = dist_dir / dmg_name
    if target_dmg.exists():
        target_dmg.unlink()


def build_bundle(
    *,
    project_name: str,
    project_version: str,
    entry_script: Path,
    py2app_options: dict[str, Any],
    dist_dir: Path,
    build_dir: Path,
    bundle_name: str,
) -> Path:
    ensure_pyobjc_installed()
    ensure_py2app_installed()

    if not entry_script.exists():
        raise SystemExit(f"Entry script not found: {entry_script}")

    os.chdir(ROOT)
    sys.path.insert(0, str(SRC_DIR))

    dist_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)

    script_args = ["py2app", "--dist-dir", str(dist_dir), "--bdist-base", str(build_dir)]

    log("Running py2app")
    from setuptools import setup  # pylint: disable=import-outside-toplevel

    setup(
        name=project_name,
        version=project_version,
        app=[str(entry_script)],
        options={"py2app": py2app_options},
        setup_requires=["py2app"],
        script_args=script_args,
    )

    produced = dist_dir / f"{entry_script.stem}.app"
    final_bundle = dist_dir / f"{bundle_name}.app"
    if produced != final_bundle:
        if final_bundle.exists():
            shutil.rmtree(final_bundle)
        if produced.exists():
            produced.rename(final_bundle)
    return final_bundle


def create_dmg(app_bundle: Path, *, dmg_name: str, volume_name: str) -> Path:
    dmg_path = app_bundle.parent / dmg_name
    if shutil.which("hdiutil") is None:
        raise SystemExit("hdiutil not found. Install Xcode command-line tools.")
    command = [
        "hdiutil",
        "create",
        "-volname",
        volume_name,
        "-srcfolder",
        str(app_bundle),
        "-ov",
        "-format",
        "UDZO",
        str(dmg_path),
    ]
    run_command(command)
    return dmg_path


def main() -> None:
    ensure_macos()
    project_name, project_version, py2app_options, plist_cfg, dist_dir, build_dir, bundle_name = prepare_configuration()
    dmg_name = f"{bundle_name}-{project_version}.dmg"
    clean_previous_outputs(dist_dir, build_dir, bundle_name, dmg_name)

    app_bundle = build_bundle(
        project_name=project_name,
        project_version=project_version,
        entry_script=ENTRY_SCRIPT,
        py2app_options=py2app_options,
        dist_dir=dist_dir,
        build_dir=build_dir,
        bundle_name=bundle_name,
    )

    log("Creating DMG")
    dmg_path = create_dmg(app_bundle, dmg_name=dmg_name, volume_name=plist_cfg.get("CFBundleDisplayName", bundle_name))

    log(f"Bundle ready: {app_bundle}")
    log(f"DMG ready: {dmg_path}")


if __name__ == "__main__":
    main()
