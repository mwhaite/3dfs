# Packaging & distribution

3dfs ships helper scripts for packaging the desktop shell on every major platform. Each script lives under `scripts/` and can run locally or in continuous integration. Start by creating and activating the project virtual environment (see [Getting started](getting-started.md)).

## Linux AppImage

```bash
python scripts/build_appimage.py --appimagetool /path/to/appimagetool
```

The helper stages the sources directly into an AppDir, injects launchers and metadata from `appimage/`, and runs `appimagetool` (when available) to emit `dist/linux/three-dfs-<version>.AppImage`. No PyInstaller step is involved; the AppDir contains the project in editable form plus its dependencies.

Use `--skip-appimagetool` to leave a ready-to-package `AppDir` on disk or `--allow-non-linux` when running in CI environments that emulate Linux. Point `--appimagetool` at a downloaded binary to produce the final artifact. Clear error messages explain when dependencies such as `appimagetool` are missing.

## Linux Flatpak

```bash
python scripts/build_flatpak.py
```

Requires `flatpak-builder` and `flatpak`. By default the script writes a repository to `dist/flatpak/repo` and a bundle file (defaults to `three-dfs.flatpak`). Use `--help` for custom bundle names, build directories, or target architectures.

## Debian package

```bash
python scripts/build_deb_package.py
```

The script stages the project, copies launcher metadata from `packaging/deb/`, and runs `dpkg-deb` to produce `.deb` artifacts under `dist/deb/`. Override architecture, control-file values, or output directories with CLI switches (`--help` lists the options).

## Windows bundle

```powershell
python scripts/build_windows_bundle.py --zip --bundle-openscad "C:\\Program Files\\OpenSCAD\\openscad.exe"
```

Run from a Windows virtual environment. The GitHub workflow invokes `python -m cx_Freeze build` and zips the resulting folder to `dist/windows/three-dfs-windows.zip`. Provide the same command locally (after `pip install .[build123d] cx_Freeze`) to mirror the build.

## macOS bundle

```bash
python scripts/build_macos_bundle.py
```

Run on macOS with the PyObjC toolchain installed (use `pip install -r packaging/macos/requirements.txt` to grab `py2app` and the Cocoa/Quartz bridges). The helper reads `pyproject.toml`, cleans previous artifacts, runs `py2app`, and emits both `dist/macos/three-dfs.app` and `dist/macos/three-dfs-<version>.dmg`. There are no flags or optional modes—the script always performs the full packaging step the CI workflow runs.

> **Note:** build123d/pyocc is not bundled on macOS because the upstream wheels do not support Apple Silicon yet. All other customizer features remain available, but operations that rely on the build123d backend are skipped and surface a clear warning. Linux/Windows builds continue to ship the dependency via the optional `build123d` extra.

## Continuous integration

The GitHub Actions workflow `.github/workflows/package.yml` invokes the same scripts on Ubuntu, macOS, and Windows.

- **Automatic trigger:** any push of a tag matching `v*`.
- **Manual trigger:** GitHub → *Actions* → *Build Packages* → **Run workflow**.

Each job uploads the resulting artifacts—AppImage/Flatpak/Deb, macOS `.app`/DMG, and Windows bundles—following the layout above.

### Running the workflow locally with `act`

[`act`](https://github.com/nektos/act) can execute the workflow locally using Docker:

```bash
act workflow_dispatch -W .github/workflows/package.yml
```

Add `-j linux-packages`, `-j macos-packages`, or `-j windows-packages` to focus on a single job. Ensure Docker is installed and `docker ps` works before launching `act`. The first run downloads base images and may take a while. The workflow mounts the repository and produces artifacts under `dist/` just like GitHub Actions.

Refer back to the [development guide](development.md#release-automation) for additional notes on release cadence and versioning.
