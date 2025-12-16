# Development guide

This guide summarises the day-to-day tooling used by contributors. Refer back to [Getting started](getting-started.md) for environment creation and to the [coding standards](coding-standards.md) for stylistic conventions.

## Core commands

| Goal | Command |
| --- | --- |
| Create/activate environment | `./setup.sh`, `./setup.sh --activate`, or `source setup.sh` |
| Run the desktop shell | `hatch run three-dfs` or `python -m three_dfs` |
| Lint | `hatch run lint` (Ruff + Black check) |
| Auto-fix style | `ruff check --fix src tests && black src tests` |
| Tests | `hatch run test` (pytest + coverage) |
| Build wheel/sdist | `hatch build` |

Keep feature branches scoped to a single change set (for example `feature/import-sketchfab`) and prefer imperative commit messages such as “Add plugin registry tests”.

## Testing strategy

Tests live under `tests/` and generally mirror the layout of `src/three_dfs/`. Use `pytest` fixtures from `tests/conftest.py` to share setup logic, and place binary fixtures under `tests/fixtures/`. Maintain deterministic behaviour—avoid network calls or machine-specific paths. When a failure is isolated to one module, run a focused subset, e.g.:

```bash
pytest tests/storage/test_repository.py -q
```

Refer to the [manual testing checklist](manual-testing.md) for smoke tests that exercise the desktop workflow before a release.

## Troubleshooting

- Recreate the environment: `./setup.sh --recreate`.
- Reset the asset catalogue: `scripts/reset_assets_db.py --dry-run`, then rerun without `--dry-run` (optionally add `--yes`).
- Missing thumbnails after a purge: launch the application and open the affected container; thumbnails regenerate lazily.

## Container metadata

Each container asset stores a structured `container_metadata` block that powers the README and metadata tab. The schema tracks `due_date`, `printed_status` (`not_started`, `in_progress`, `printed`, `deprecated`), `priority` (`low`/`normal`/`high`/`urgent`), free-form `notes`, `contacts[]` (name/role/email/url/notes), and `external_links[]` (label/url/kind/description). Components keep their existing metadata; only the container root owns this block.

When adding or editing a container, populate the block via `three_dfs.container.apply_container_metadata` or the UI’s **Edit Metadata** button (available in the container pane). Invalid ISO dates, contacts without names, or links without URLs are rejected early. Developers can run:

```bash
hatch run verify-metadata
# or directly:
python scripts/migrate_container_metadata.py --dry-run
```

Include `--db` to point at a specific SQLite file, and `--verbose` if you need per-container logging. CI runs the same `verify-metadata` command (see `.github/workflows/ci.yml`). When metadata changes, regenerate any README “ABOUT” panes and re-run the verification before pushing.

## Release automation

Packaging is handled by the scripts documented in [Packaging & distribution](packaging.md). The GitHub Actions workflow `.github/workflows/package.yml` drives the same scripts on Ubuntu, macOS, and Windows. Tagging a commit with `v*` triggers the workflow automatically; use the *Build Packages* action to run it manually. When preparing a release:

1. Ensure `hatch run lint` and `hatch run test` pass locally.
2. Update release notes and version metadata in `pyproject.toml` if required.
3. Tag the release (`git tag vX.Y.Z && git push --tags`).
4. Monitor the workflow to confirm bundles were produced successfully.

Adopt the same steps when running the workflow locally with [`act`](packaging.md#running-the-workflow-locally-with-act).
