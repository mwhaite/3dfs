# Packaging Environment Requirements

This document outlines the system-level dependencies and tools required to build distribution packages for 3dfs on each platform.

## General Requirements

All packaging builds require Python 3.11+ with the project dependencies installed:

```bash
pip install -e .
```

## Linux (AppImage)

**System requirements:**
- Linux kernel (x86_64 recommended)
- `patchelf` – modify ELF binary interpreter paths
- `libfuse2` – required to mount and run AppImage bundles

**Installation:**
```bash
# Ubuntu/Debian
sudo apt-get install patchelf libfuse2

# Fedora/RHEL
sudo dnf install patchelf fuse2

# Arch
sudo pacman -S patchelf fuse2
```

**Python requirements:**
```bash
pip install pyinstaller
```

**Additional tools:**
- `appimagetool` – generates the final AppImage binary (download from [AppImageKit releases](https://github.com/AppImage/AppImageKit/releases))

**Build command:**
```bash
python scripts/build_appimage.py --appimagetool /path/to/appimagetool-x86_64.AppImage
```

### Notes
- PyInstaller requires Python to be built with `--enable-shared` for shared library support
- The build container should use glibc 2.29 or earlier for maximum compatibility (AppImage standard recommendation)

## Linux (Flatpak)

**System requirements:**
- `flatpak` – Flatpak runtime and SDK
- `flatpak-builder` – build tool
- `desktop-file-utils` – validates desktop entry files

**Installation:**
```bash
# Ubuntu/Debian
sudo apt-get install flatpak flatpak-builder desktop-file-utils

# Fedora/RHEL
sudo dnf install flatpak flatpak-builder desktop-file-utils

# Arch
sudo pacman -S flatpak flatpak-builder desktop-file-utils
```

**Runtime setup:**
```bash
# Add Flathub repository
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo

# Install required runtime and SDK (example: Freedesktop 23.08)
flatpak install flathub org.freedesktop.Platform//23.08 org.freedesktop.Sdk//23.08
```

**Build command:**
```bash
python scripts/build_flatpak.py
```

## Linux (Debian Package)

**System requirements:**
- `dpkg-deb` – part of the dpkg suite (usually pre-installed)
- `patchelf` – may be required for some dependencies

**Installation:**
```bash
# Ubuntu/Debian (usually pre-installed)
sudo apt-get install dpkg

# Fedora/RHEL
sudo dnf install dpkg
```

**Build command:**
```bash
python scripts/build_deb_package.py
```

## macOS

**System requirements:**
- macOS 10.13+ (Big Sur recommended for Qt6 support)
- Xcode Command Line Tools (for codesigning)
- `hdiutil` – included with macOS

**Installation:**
```bash
xcode-select --install
```

**Python requirements:**
```bash
pip install pyinstaller
```

**Build command:**
```bash
python scripts/build_macos_bundle.py --create-dmg
```

**Optional:**
- Codesigning identity: `python scripts/build_macos_bundle.py --codesign-id "Developer ID Application: Your Name (XXXXXXXXXX)"`
- Icon file (`.icns`): `python scripts/build_macos_bundle.py --icon /path/to/icon.icns`

## Windows

**System requirements:**
- Windows 10 or later
- Visual C++ redistributables (included with Python)

**Python requirements:**
```bash
pip install pyinstaller
```

**Build command:**
```bash
python scripts/build_windows_bundle.py
```

**Optional:**
- Single-file executable: add `--onefile`
- Icon file (`.ico`): `--icon /path/to/icon.ico`
- ZIP archive: add `--zip`
- OpenSCAD runtime: `--bundle-openscad "C:\Program Files\OpenSCAD\openscad.exe"`

## CI/CD Environments

### GitHub Actions

The repository includes a GitHub Actions workflow (`.github/workflows/package.yml`) that builds all formats:

```bash
# Trigger manually from GitHub → Actions → Build Packages → Run workflow
# or automatically on tags: git tag v0.1.0 && git push --tags
```

### Local Testing with `act`

To test the CI workflow locally using Docker:

```bash
# Install act
brew install act    # macOS
# or download from https://github.com/nektos/act

# Run the workflow
act workflow_dispatch -W .github/workflows/package.yml

# Run a specific job
act workflow_dispatch -W .github/workflows/package.yml -j linux-packages
```

## Troubleshooting

### AppImage won't run on older systems
- Build on a system with glibc 2.29 or earlier
- Use `APPIMAGE_DEBUG=1 ./three-dfs-x86_64.AppImage` to see detailed error messages

### PyInstaller fails with "Python was built without a shared library"
- Use a Python built with `--enable-shared` (e.g., from `pyenv`, `conda`, or official installers)
- Avoid system Python on some Linux distributions

### Missing dependencies in bundles
- Check the packaging script's `collect_targets` and `hidden_imports` lists
- Add missing modules via `--collect-all` or `--hidden-import` flags

### Qt/PySide6 issues in AppImage
- Ensure `LD_LIBRARY_PATH` is set correctly (automatically done by our `AppRun` script)
- Run with `APPIMAGE_DEBUG=1` to see library loading diagnostics

## Environment Variables

### AppImage (all platforms with AppImage)
- `APPIMAGE_DEBUG=1` – enable verbose output from launcher scripts (shows path resolution, library paths, etc.)

### Flatpak (Linux)
- `FLATPAK_DISABLE_ROFILES_FUSE=1` – disable FUSE overlays (useful in restricted environments)
- Already set in GitHub Actions for better CI compatibility

## References

- [AppImage Documentation](https://docs.appimage.org/)
- [Flatpak Documentation](https://docs.flatpak.org/)
- [PyInstaller Documentation](https://pyinstaller.org/)
- [Debian Packaging Guide](https://www.debian.org/doc/manuals/debian-faq/pkg-basics.en.html)
