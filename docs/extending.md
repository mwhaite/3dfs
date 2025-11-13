# Extending 3dfs

3dfs exposes multiple extension points so teams can integrate additional asset sources, customise build pipelines, or automate project maintenance.

## Import plugins

Remote sources integrate through the `ImportPlugin` protocol in `three_dfs.import_plugins`.

1. Implement `can_handle(source: str) -> bool` to declare whether the plugin understands a particular identifier.
2. Implement `fetch(source: str, destination: Path) -> Metadata` to download the asset into the provided directory and return metadata describing the file (for example `remote_source`, `extension`, or `label`).
3. Register the plugin with `register_plugin` or expose it through the `three_dfs.import_plugins` entry point group so it loads automatically.

The `scaffold_plugin` helper accelerates prototyping:

```python
from pathlib import Path
from three_dfs.import_plugins import scaffold_plugin

plugin_path = scaffold_plugin("Sketchfab", Path("./plugins"))
print(f"Plugin scaffold written to {plugin_path}")
```

Fill in the generated hooks, ensure the downloaded file is saved to `destination`, and expose the module via entry points. During imports the first plugin whose `can_handle` returns `True` performs the fetch and its metadata is merged into the stored asset record.

## Customizer backends

Parametric engines integrate via the [`CustomizerBackend` protocol](customizer-backends.md). Backends translate source annotations into a `ParameterSchema`, validate user overrides, and produce a `CustomizerSession` describing how to run the build. Review the [transformation helpers](customizer-transformations.md) for reusable mesh operations (scale, translate, emboss, boolean union, and more) that you can compose into backend plans.

## Automation hooks

Library metadata lives in SQLite under `~/.3dfs/assets.sqlite3`. Managed assets mirror the configured library root, so external automation can add or update files and let the desktop shell rescan changes. When the automation creates new parametric sources, record their metadata using the importer to keep provenance intact.

Consult the [development guide](development.md) for information about running the automated test suite, packaging releases, and contributing changes upstream.
