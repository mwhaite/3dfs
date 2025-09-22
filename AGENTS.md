# Repository Guidelines

## Project Structure & Modules
- `src/three_dfs/`: main package (src layout)
  - `app.py`: CLI/entry (`three-dfs`).
  - `importer.py` and `import_plugins/`: asset import system.
  - `storage/`, `db/`, `data/`: persistence and models.
  - `ui/`, `thumbnails/`: PySide6 UI and image generation.
- `tests/`: pytest suite (e.g., `tests/storage/test_repository.py`).
- `docs/`: engineering notes; update when modifying public APIs.
- `pyproject.toml`: build, tooling, and test config.

## Build, Test, and Run
- Quick start: `bash setup.sh && source .venv/bin/activate`.
- Create env: `hatch env create` — installs dev deps.
- Lint/format check: `hatch run lint` — runs Ruff and Black in check mode.
- Tests + coverage: `hatch run test` — pytest with coverage report.
- Run app (CLI): `hatch run three-dfs` or `python -m three_dfs`.
- Build distributions: `hatch build` — creates wheel/sdist from `pyproject.toml`.

## Coding Style & Naming
- Python 3.11, 4‑space indent, type hints encouraged for new/changed code.
- Black (line length 88): `black src tests` to format.
- Ruff rules: `E,F,I,B,UP,W`; fixable: `ruff check --fix src tests`.
- Naming: packages/modules `snake_case`, classes `PascalCase`, functions/vars `snake_case`, constants `UPPER_CASE`.
- Keep modules focused (e.g., storage logic in `three_dfs/storage/`).

## Testing Guidelines
- Framework: pytest; tests live in `tests/` and named `test_*.py`.
- Mirror structure where practical (e.g., `src/three_dfs/db/models.py` → `tests/db/test_models.py`).
- Use fixtures from `tests/conftest.py`; place assets under `tests/fixtures/`.
- Run locally: `hatch run test`; prefer fast, deterministic tests (no network in unit tests).

## Commit & Pull Request Guidelines
- Commits: concise, imperative mood (e.g., "Add thumbnail rendering"), group related changes.
- PRs: clear description, linked issues, scope-limited diff, screenshots/GIFs for UI changes.
- Requirements: green CI, `hatch run lint` and `hatch run test` pass, docs updated when touching public APIs or CLI.
- Avoid drive-by reformatting; apply formatting only to touched lines/files.

## Security & Configuration
- Do not commit secrets; external plugin credentials must use environment variables or OS keychain.
- Validate file paths in import plugins; write only to provided destinations.
- Target Python 3.11; rely on Hatch environments for reproducibility.
