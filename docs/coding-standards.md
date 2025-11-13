# Coding standards

This document outlines the baseline conventions for contributing to the 3dfs codebase. Pair it with the [development guide](development.md) for day-to-day commands and workflow expectations.

## Python version

- Target **Python 3.11** or newer.
- Prefer modern standard-library features (for example `pathlib`, dataclasses, and `typing.Self`) when they simplify code.

## Formatting & linting

- Run `hatch run lint` before opening a pull request. The command executes:
  - [`ruff`](https://docs.astral.sh/ruff/) for static analysis and import sorting.
  - [`black`](https://black.readthedocs.io/) in check mode to ensure a consistent code format.
- The project uses an 88-character line length for both Ruff and Black.
- Avoid `# noqa` or ignore comments unless there is a compelling, well-documented reason.

## Testing expectations

- Add unit tests for all new modules and behaviours. Existing tests illustrate preferred style and structure.
- Execute `hatch run test` locally to run the `pytest` suite with coverage.
- Keep tests deterministic and platform agnostic. Avoid absolute paths or machine-specific configuration.

## Type hints

- Prefer comprehensive static typing using the standard `typing` module. Future tooling may introduce optional strict checks.
- Use postponed evaluation via `from __future__ import annotations` for new modules to simplify annotation usage.

## Project structure

- Place source files under `src/three_dfs/` using the [src-layout packaging pattern](https://packaging.python.org/en/latest/discussions/src-layout/).
- Keep modules focused on a single responsibility; break larger modules into packages when appropriate.
- Use descriptive module and package names. Avoid abbreviations unless they are industry standard.

## Documentation & comments

- Provide module docstrings summarising purpose and usage.
- Write clear inline comments only when intent is not obvious from the code.
- Update the relevant documentation (for example the [user guide](user-guide.md) or [customizer references](customizer-backends.md)) when behaviour or external interfaces change.

## Commit messages

- Use imperative mood (e.g. “Add customizer preview skeleton”).
- Reference related issues or roadmap items when possible.

Following these standards keeps the codebase coherent and approachable as 3dfs evolves.
