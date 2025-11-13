# 3dfs

3dfs is a PySide6 desktop shell for managing large 3D asset libraries. It indexes containers, records provenance metadata, and embeds a parametric customization pipeline so derivative models stay linked to their sources.

## Quick start

1. Bootstrap the environment and launch the app:
   ```bash
   ./setup.sh
   ```
2. Keep the virtual environment active without launching the UI:
   ```bash
   ./setup.sh --activate
   # or
   source setup.sh
   ```
3. Run the desktop shell from an activated environment:
   ```bash
   hatch run three-dfs
   # or
   python -m three_dfs
   ```
4. Execute the automated checks:
   ```bash
   hatch run lint
   hatch run test
   ```

## Documentation

The full documentation is published via GitHub Pages from the [`docs/`](docs/index.md) directory:

- [Project overview](docs/overview.md) – high-level concepts and primary workflows.
- [Getting started](docs/getting-started.md) – environment setup, commands, and configuration.
- [User guide](docs/user-guide.md) – repository explorer, preview tabs, and project workspace.
- [Packaging & distribution](docs/packaging.md) – building AppImage, Flatpak, Debian, Windows, and macOS bundles.
- [Architecture](docs/architecture.md) – runtime diagram and subsystem summary.
- [Development guide](docs/development.md) and [coding standards](docs/coding-standards.md) – contributor workflow and conventions.
- [Customization references](docs/customizer-backends.md) and [transformation helpers](docs/customizer-transformations.md) – extend parametric backends and compose reusable mesh operations.

Issues, releases, and CI logs live in the GitHub repository. Contributions are welcome—start with the development guide above and open a pull request when you are ready.
