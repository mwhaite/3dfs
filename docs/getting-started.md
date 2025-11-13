# Getting started

This guide walks through creating a local environment, running automated checks, and launching the desktop shell. It assumes a Python 3.11+ toolchain and a system capable of running PySide6 applications.

## Quick setup

Clone the repository and run the helper script from the project root:

```bash
./setup.sh
```

The script creates a `.venv`, installs dependencies, and launches the application. Re-run it with `--activate` (or `source setup.sh`) to activate the virtual environment without starting the UI.

Once the environment is active you can launch the shell directly:

```bash
hatch run three-dfs
# or
python -m three_dfs
```

## Automated checks

Use the `hatch` scripts to run linting and tests:

```bash
hatch run lint
hatch run test
```

The [development guide](development.md) covers the individual tools in more detail, including type checking, packaging, and release steps.

## Environment configuration

Fine-tune the runtime with environment variables:

- `THREE_DFS_LIBRARY_PATH` – override the default managed library root (`~/Models`).
- `THREE_DFS_BOOTSTRAP_DEMO=1` – seed the repository with demo entries for exploration.
- Remove `~/.3dfs/assets.sqlite3` while the app is closed to reset the metadata store.

The importer uses the library root by default but allows overrides per call via the `storage_root` parameter.

## Where to go next

- Read the [user guide](user-guide.md) for a tour of the repository explorer, preview tabs, and project workspace.
- Visit the [packaging reference](packaging.md) to produce AppImage, Flatpak, Debian, Windows, or macOS bundles.
- Explore [customization backends](customizer-backends.md) to integrate new parametric engines or extend the OpenSCAD workflow.
