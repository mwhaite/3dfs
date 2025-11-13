# Packaging Scripts

The repository ships helper scripts under `scripts/` for building platform
bundles. They do not aim to replace the official platform tooling, but they
capture the steps we run locally so the process is reproducible.

**Before building:** see [Packaging Environment Requirements](packaging_requirements.md) for system-level dependencies, tools, and installation instructions for each platform.

## Linux (AppImage)

```
python scripts/build_appimage.py
```

Produces `dist/linux/three-dfs.AppImage`. Run the script on a Linux host with
PyInstaller available.

## Linux (Flatpak)

```
python scripts/build_flatpak.py
```

Requires `flatpak-builder` and `flatpak`. The script writes a repository to
`dist/flatpak/repo` and a bundle file (defaults to `three-dfs.flatpak`). Use
`--help` for options.

## Debian Package

Build a `.deb` using `scripts/build_deb_package.py` (requires `dpkg-deb`):

```
python scripts/build_deb_package.py
```

The output lives in `dist/deb/`. Use `--help` for architecture or control-file
overrides.

## Windows

```
python scripts/build_windows_bundle.py
```

Run inside a Windows virtual environment. The script uses PyInstaller and
places the output in `dist/windows/`.

## macOS

```
python scripts/build_macos_bundle.py --create-dmg
```

Run on macOS. The script produces `<name>.app` in `dist/macos/` and optionally a
DMG (when `--create-dmg` is supplied). Use `--help` to see codesign, icon, and
other options.

## CI Workflows

We ship a GitHub Actions workflow (`.github/workflows/package.yml`) that invokes
the same scripts on Ubuntu, macOS, and Windows:

- **Automatic triggers:** any push of a tag matching `v*`.
- **Manual trigger:** open GitHub → *Actions* → *Build Packages* → **Run
  workflow**, then choose the branch/tag (defaults to the repo’s default
  branch) and confirm.

Each job archives the resulting artifacts—AppImage/Flatpak/Deb, DMG/`.app`, and
Windows bundles—using the same layout described above.

### Running the workflow locally with `act`

GitHub Actions does not run natively outside GitHub, but the
[`act`](https://github.com/nektos/act) CLI can execute the workflow on your
machine using Docker.

```
act workflow_dispatch -W .github/workflows/package.yml
```

Add `-j linux-packages`, `-j macos-packages`, or `-j windows-packages` to focus
on a single job. `act` requires Docker and pulls images matching the workflow’s
runner images (e.g. `ubuntu-latest`). The first run can take a while while the
base images download.

Before running `act`, install its prerequisites:

```
# Install Docker (brew/apt/yum/etc.) and ensure `docker ps` works.
brew install act    # or grab a release binary from GitHub
```

`act` mounts the repository and executes each step exactly as defined in the
workflow—including installing Flatpak, PyInstaller, dpkg, etc.—so the resulting
artifacts will appear under `dist/` just like in CI. If you prefer not to use
Docker, replicate the individual script invocations in the sections above.
