# 3dfs documentation

3dfs is a PySide6 desktop shell for organising, previewing, and customising 3D asset libraries. These pages provide a cohesive reference for users who want to explore the UI, administrators preparing environments, and contributors extending the platform.

## Start here

- [Project overview](overview.md) – discover the main concepts, supported workflows, and UI highlights.
- [Getting started](getting-started.md) – set up the environment, run automated checks, and launch the desktop shell.
- [User guide](user-guide.md) – learn how to browse repositories, preview assets, manage projects, and work with linked components.
- [Packaging & distribution](packaging.md) – build platform-specific bundles or invoke the GitHub Actions workflow.

## Deep dives and references

- [Architecture](architecture.md) – runtime diagram, storage model, and major subsystems.
- [Customization backends](customizer-backends.md) and [transformation helpers](customizer-transformations.md) – implement or extend parametric pipelines.
- [Importer plugins](extending.md#import-plugins) – integrate remote sources with the managed library.
- [Development environment](development.md) – tooling, test strategy, and release processes for contributors.
- [Coding standards](coding-standards.md) – conventions for Python, Qt widgets, and tests.
- [Manual testing checklist](manual-testing.md) – smoke tests that verify the desktop workflow end to end.
- [Example assets](examples/openscad/README.md) – sample OpenSCAD projects that exercise the customization pipeline.

## Additional resources

The [GitHub repository](https://github.com/) hosts issue tracking, release notes, and automated build logs. Use the "Edit this page" link in GitHub Pages to propose improvements or report outdated details.
