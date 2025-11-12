# 3dfs

3dfs is a PySide6 desktop shell for managing, previewing, and customising 3D asset libraries. The application persists asset metadata, tags, and derivative relationships in SQLite while storing managed asset copies under a configurable library root.

## Features

* Repository browser, preview pane, and project workspace built with PySide6 widgets for a cohesive desktop experience.
* Thumbnail extraction, metadata inspection, and an integrated OpenGL viewer for STL, OBJ, PLY, GLB/GLTF, and FBX meshes (FBX requires the Autodesk FBX SDK; other meshes rely on `trimesh`). STEP assets render via bounding boxes when full meshes are unavailable.
* Parametric customization pipeline with an OpenSCAD backend that records derivative assets and parameter schemas.
* Project inspector that discovers arrangement scripts and attachments with live filesystem refresh, on-demand metadata cleanup for missing files, and a dedicated link browser for navigating between related containers.
* Container linking workflow that establishes relationships between containers, including version-aware links and an "Import From Linked Container" action that references remote components without duplicating files on disk.
* Pluggable importer that handles local files and remote identifiers via fetcher plugins and records detailed mesh metadata.

## Getting Started

1. Quick setup and launch:

   ```bash
   ./setup.sh
   ```

   This creates `.venv`, installs dependencies, and runs the desktop shell. Use `./setup.sh --activate` or `source setup.sh` to keep the environment active.

2. Run the automated checks:

   ```bash
   hatch run lint
   hatch run test
   ```

3. Launch the application after setup or from an activated environment:

   ```bash
   hatch run three-dfs
   # or
   python -m three_dfs
   ```

## Linux AppImage packaging

The repository includes a helper that freezes the desktop shell with PyInstaller
and stages an AppDir suitable for AppImage distribution. Run the script from a
Linux environment where the project dependencies (and PyInstaller) are
installed:

```bash
python scripts/build_appimage.py --appimagetool /path/to/appimagetool
```

The script performs the following steps:

1. Invokes PyInstaller to create a one-folder build of `three_dfs.app`.
2. Copies launchers, desktop metadata, and icons from `appimage/` into a fresh
   `AppDir`.
3. Runs `appimagetool` when provided to emit `dist/linux/three-dfs-<version>.AppImage`.

Use `--skip-appimagetool` to leave a ready-to-package `AppDir` on disk or
`--allow-non-linux` when running in CI environments that emulate Linux. Pass
`--collect-all`/`--hidden-import` flags directly to PyInstaller for additional
packaging tweaks. The helper surfaces clear errors when PyInstaller or
`appimagetool` are not available.

## Linux Flatpak packaging

`scripts/build_flatpak.py` wraps `flatpak-builder` to produce a repository and
bundle file:

```bash
python scripts/build_flatpak.py
```

By default the script writes to `dist/flatpak/three-dfs.flatpak`. Use
`--help` for options such as custom bundle names, build directories, or target
architectures.

## Debian packaging

`scripts/build_deb_package.py` stages the project and runs `dpkg-deb` to produce
`.deb` artifacts:

```bash
python scripts/build_deb_package.py
```

The script installs into a staging directory, copies launcher/desktop files
from `packaging/deb/`, and writes the resulting package to `dist/deb/`.

## Windows bundling

`scripts/build_windows_bundle.py` mirrors the manual PyInstaller steps for a
Windows deployment:

```bash
python scripts/build_windows_bundle.py
```

Run the script inside a Windows virtual environment. The resulting bundle lives
under `dist/windows/` and can optionally include a zipped archive or OpenSCAD
runtime.

## macOS bundling

`scripts/build_macos_bundle.py` constructs a `.app` bundle and can optionally
wrap it in a DMG:

```bash
python scripts/build_macos_bundle.py --create-dmg
```

Run on macOS; use `--help` to see codesign, icon, and other switches. Output is
written to `dist/macos/`.

## Windows packaging

Run `scripts/build_windows_bundle.py` on a Windows workstation (inside the
project's virtual environment) to freeze the desktop shell into a distributable
executable using PyInstaller. The script mirrors the recommended settings for
collecting Qt plugins and other native dependencies and accepts optional flags
for advanced scenarios:

```powershell
python scripts/build_windows_bundle.py --zip --bundle-openscad "C:\\Program Files\\OpenSCAD\\openscad.exe"
```

Key options include:

* `--onefile` – emit a single-file executable instead of the default folder
  distribution.
* `--icon` – provide a `.ico` file that becomes the Windows application icon.
* `--bundle-openscad` – copy an existing `openscad.exe` into the bundle so the
  OpenSCAD customizer backend works out of the box.
* `--zip` – archive the output directory after a successful build for easier
  distribution.

4. Project layout:

   ```text
   .
   ├── docs/                  # Architecture and workflow documentation
   ├── src/three_dfs/         # Application package (UI, importer, storage, customizer, ...)
   ├── tests/                 # Pytest suite
   ├── pyproject.toml         # Build system, dependencies, tooling config
   └── README.md              # This document
   ```

## User interface overview

### Repository explorer

The main window loads persisted assets into a searchable repository list. Context menus expose operations such as opening folders or toggling the repository sidebar. Quick actions surface derivative assets generated by the customization pipeline so you can navigate between related items without rescanning the library.

### Preview and customization

Selecting an asset populates the preview pane with thumbnails, metadata, and optional descriptions. The four tabs cover the common flows:

- **Thumbnail** – cached previews rendered from the on-disk asset (or an informative message while a snapshot is missing).
- **3D Viewer** – interactive OpenGL mesh viewer for `.stl`, `.obj`, `.ply`, `.fbx`, `.gltf`, or `.glb` models. FBX rendering still requires the Autodesk FBX SDK; other meshes rely on `trimesh`.
- **Text** – plaintext or markdown previews for source files, arrangement scripts, and README-style documents.
- **Customizer** – when an asset advertises a parametric backend, the embedded dialog exposes inputs and derivative launches.

When decoding fails the corresponding tab disables itself with an explanatory tooltip so users never land on an empty panel. The metadata callout records the failure reason (for example missing codecs or oversized binaries) to aid troubleshooting.

### Project workspace

Projects open in a dedicated pane that lists components, attachments, arrangement scripts, outgoing links, and inbound "Linked Here" references. Each project corresponds to a directory inside the configured library root and the application treats its immediate children as components, but linked components are recorded as metadata entries that reference a remote file path. Containers are strictly flat; they do not contain any sub-directories. All files and links within a container reside directly in its root directory. Users can search components, refresh the current folder, launch attachments, import additional components from linked containers, or navigate to related containers. File deletions clean up their metadata entries (even if the file is already missing) before dispatching a rescan request, while the filesystem watcher keeps the pane in sync with on-disk changes. Removing a linked component only drops the metadata reference and never touches the source container on disk.

### Container links & linked components

The **Links** list shows every outgoing relationship created through **Link Container…**. Selecting a link focuses the remote container, and context actions expose **Import From Linked Container…**, which opens a tree dialog listing each linked container (and optionally a specific linked version). Choosing an entry adds a `linked_component` record to the current container so the model appears in the Components list with italic styling and a tooltip back to its origin. Because the linked component remains on disk only in the source container, CRUD operations respect provenance: deleting the entry detaches the link but keeps the source file untouched, while refreshing a container preserves all linked components even if a filesystem scan runs in the background. Inbound references from other containers show up under **Linked Here**, providing a quick view of who depends on the current container without exposing them as import candidates.

## Asset library & storage

Asset metadata is persisted in SQLite (`~/.3dfs/assets.sqlite3` by default) and managed through the `AssetService`. The service tracks legacy tags (now hidden in the UI), customizations, derivative relationships, and thumbnail caches while exposing helpers to bootstrap demo data. All managed copies live under the configured library root, ensuring reproducible paths across sessions.

## Importing assets

Use `three_dfs.importer.import_asset` to register local files or remote identifiers. The importer copies supported formats (`.stl`, `.obj`, `.step`, `.stp`) into managed storage, extracts mesh metadata such as vertex/face counts and bounding boxes with `trimesh`, and records provenance fields on the resulting asset. When a path cannot be resolved locally the importer delegates to registered plugins, normalises returned metadata, and enforces that the fetched asset matches one of the supported extensions.

## Customization workflow

The `three_dfs.customizer` package exposes a protocol for parameterised backends and an execution pipeline that stages builds under the managed library. When a customization runs, the pipeline records the parameter schema and values, persists generated artifacts as derivative assets, and links them back to the source customization for status tracking. The preview pane embeds the customizer dialog so users can rerun prior configurations, inspect parameter summaries, and open derivative outputs directly from the UI.

## Projects

Project folders can declare arrangement scripts inside `arrangements/` or `_arrangements/` directories. The discovery helpers merge newly found scripts with stored metadata, preserve custom labels, and ignore stale entries. Components classified as directories trigger navigation, while attachments remain accessible through context actions. The desktop shell watches project folders for changes and refreshes metadata after a short debounce so arrangement previews stay current.

## Tagging

The project pane exposes a tag sidebar for the active asset: use it to create,
rename, or remove tags scoped to the current container or file. Machine tags
(`Machine:<ID>`) applied to G-code files surface directly in the preview pane as
clickable links; selecting a link applies a library-wide filter so only
containers matching that tag remain visible in the repository list.

The global search entry accepts `#tag` queries to filter the repository list as
well. Typing `#machine:fabrikator` or clicking a `Machine:` link in the preview
behaves identically. Clearing the search field restores the full listing.

## Configuration

- Library root: defaults to `~/Models`. Override with `THREE_DFS_LIBRARY_PATH`.
- Demo entries: set `THREE_DFS_BOOTSTRAP_DEMO=1` to seed example assets.
- Database: remove `~/.3dfs/assets.sqlite3` while the app is closed to reset the repository.
- Importer storage: by default mirrors the configured library root; override per import via `storage_root`.

## Import plugins

Plugins implement the `ImportPlugin` protocol from `three_dfs.import_plugins`. Each plugin advertises `can_handle(source: str) -> bool` and `fetch(source: str, destination: Path) -> Metadata`, then registers itself either by calling `register_plugin` or via the `three_dfs.import_plugins` entry point group. During remote imports the first plugin reporting capability downloads the asset into the provided destination, returning metadata (for example `remote_source`, `extension`, `label`) which the importer merges into the stored record.

The `scaffold_plugin` helper generates boilerplate modules:

```python
from pathlib import Path
from three_dfs.import_plugins import scaffold_plugin

plugin_path = scaffold_plugin("Sketchfab", Path("./plugins"))
print(f"Plugin scaffold written to {plugin_path}")
```

Fill in the generated TODO hooks, ensure the plugin writes the fetched file to the supplied destination, and expose the module via an entry point to make it discoverable.
