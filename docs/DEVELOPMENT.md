# Development Guide

## Quick Start
- One-liner: `./setup.sh` — creates the venv and launches the app.
- Keep shell active after app: `./setup.sh --activate`
- Current shell activation: `source setup.sh` (runs app, leaves shell active)
- Skip running the app: add `--no-run`
- Verify: `hatch run lint && hatch run test`

## Common Commands
- Lint: `hatch run lint` (Ruff + Black check)
- Fix style: `ruff check --fix src tests && black src tests`
- Tests: `hatch run test` (pytest + coverage)
- Run app: `hatch run three-dfs` or `python -m three_dfs`
- Build: `hatch build`

## Workflow
- Branching: feature branches from `main` (e.g., `feature/import-sketchfab`).
- Commits: imperative mood, scoped (e.g., "Add plugin registry tests").
- PRs: include description, linked issues, screenshots/GIFs for UI.
- CI: ensure `hatch run lint` and `hatch run test` pass locally first.

## Testing
- Framework: `pytest` with coverage. Place tests under `tests/` as `test_*.py`.
- Mirror structure of `src/three_dfs/` where practical.
- Use shared fixtures in `tests/conftest.py`; put assets under `tests/fixtures/`.
- Keep tests deterministic; avoid network and system‑specific paths.

## Code Style
- Python 3.11, 4‑space indent, type hints on new/changed code.
- Black line length: 88; Ruff rules: E,F,I,B,UP,W.
- Module naming: `snake_case`; classes `PascalCase`; constants `UPPER_CASE`.

## Running Locally
- First run may create thumbnails and DB files under the managed paths.
- Importing remote assets uses plugins (`three_dfs.import_plugins`). Ensure they write only to provided destinations.

## Troubleshooting
- Hatch issues: recreate env `bash setup.sh --recreate`.
- Lint failures: run the "Fix style" command above.
- Failing tests: run selectively, e.g., `pytest tests/storage/test_repository.py -q`.
- Resetting the catalog: run `scripts/reset_assets_db.py --dry-run` to preview files, then re-run without `--dry-run` (optionally add `--yes`) to wipe the SQLite database under `~/.3dfs/`.
