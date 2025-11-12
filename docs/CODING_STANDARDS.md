# Coding Standards

This document outlines the baseline conventions for contributing to the
3dfs codebase. The intent is to encourage a predictable, maintainable
code style that can scale as the application grows.

## Python Version

- Target **Python 3.11** or newer.
- Prefer using modern standard-library features (e.g. `pathlib`,
  dataclasses, `typing.Self`) whenever they simplify the code.

## Formatting & Linting

- Run `hatch run lint` before opening a pull request. The command executes:
  - [`ruff`](https://docs.astral.sh/ruff/) for static analysis and import
    sorting.
  - [`black`](https://black.readthedocs.io/) in check mode to ensure a
    consistent code format.
- The project uses an 88-character line length for both Ruff and Black.
- Avoid `# noqa` or ignore comments unless there is a compelling,
  well-documented reason.

## Testing

- Add unit tests for all new modules and behaviours. Existing tests may
  be a guide for style and structure.
- Execute `hatch run test` locally to run the `pytest` suite with code
  coverage reporting.
- Structure tests to be deterministic and platform agnostic. Avoid
  relying on absolute paths or system-specific configurations.

## Type Hints

- Prefer comprehensive static typing using the standard `typing` module.
  Future tooling may introduce optional strict type checking.
- Use [PEP 563](https://peps.python.org/pep-0563/) style postponed
  evaluation via `from __future__ import annotations` for new modules to
  simplify annotation usage.

## Project Structure

- Place source files under `src/three_dfs/` using
  [src-layout packaging](https://packaging.python.org/en/latest/discussions/src-layout/).
- Keep modules focused on a single responsibility; break larger modules
  into packages when appropriate.
- Use descriptive module and package names. Avoid abbreviations unless
  they are industry standard.

## Documentation & Comments

- Provide module docstrings summarizing purpose and usage.
- Write clear inline comments only when the intent is not obvious from
  the code itself.
- Update the project documentation (`README.md`, design docs) when
  behaviour or external interfaces change.

## Commit Messages

- Use imperative mood (e.g. "Add customizer preview skeleton").
- Reference related issues or roadmap items when possible.

Following these standards will help maintain a coherent and approachable
codebase as 3dfs evolves.
