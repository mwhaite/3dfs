# Project overview

3dfs is a desktop companion for large 3D model libraries. It organises models into containers, records provenance metadata, and embeds a parametric customization pipeline so derivative assets stay linked to their source files. The desktop shell is implemented with PySide6 widgets for a native workflow across platforms.

## Core capabilities

- **Repository browser** – filter and search containers, inspect relationships, and launch linked assets without leaving the application.
- **Rich preview pane** – switch between thumbnails, an OpenGL mesh viewer for STL/OBJ/PLY/FBX/GLB files, a text previewer, and the customization dialog.
- **Project workspace** – group arrangements, attachments, linked containers, and inbound references inside a single pane with automatic filesystem refresh.
- **Managed asset store** – persist metadata in SQLite while mirroring managed files in a configurable library root.
- **Parametric customization** – execute OpenSCAD (and other backends) with recorded parameter schemas, derivative tracking, and re-runnable histories.
- **Importer framework** – register plugins that fetch remote assets, normalise metadata, and drop results into managed storage.

## How the pieces fit together

The [architecture overview](architecture.md) documents the runtime flow from entry points to the UI, importer, storage layer, and customization pipeline. For backend-specific details see the [customizer backend guide](customizer-backends.md) and [transformation helpers](customizer-transformations.md).

If you are extending the desktop shell or building automation around it, continue with the [development guide](development.md) and [coding standards](coding-standards.md). Those pages cover the project layout, tooling, and conventions that keep the codebase consistent.

## Next steps

- Follow the [getting started guide](getting-started.md) to bootstrap a development environment and launch the application.
- Explore the [user guide](user-guide.md) to understand the repository explorer, preview tabs, and linking workflows.
- Visit the [packaging reference](packaging.md) when you are ready to build platform-specific bundles or run the GitHub Actions workflow.
