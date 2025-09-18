# 3dfs

Cross-platform 3D file explorer and customization platform. This
repository currently contains the scaffolding for the Python-based
application, including build tooling, testing, linting, and coding
standards documentation.

## Getting Started

1. Install the project dependencies using [Hatch](https://hatch.pypa.io/):

   ```bash
   pip install hatch
   hatch env create
   ```

2. Run the automated checks:

   ```bash
   hatch run lint
   hatch run test
   ```

3. Explore the project structure:

   ```text
   .
   ├── docs/                  # Engineering documentation
   │   └── CODING_STANDARDS.md
   ├── src/
   │   └── three_dfs/         # Application package (src layout)
   ├── tests/                 # Pytest-based unit tests
   ├── .github/workflows/     # Continuous integration pipelines
   ├── pyproject.toml         # Build system and tooling configuration
   └── README.md              # Project overview
   ```

Additional design documents, architectural notes, and implementation
code will be layered onto this foundation in subsequent milestones.

## Import plugins

The asset importer supports remote sources via a lightweight plugin
interface. Plugins implement the ``ImportPlugin`` protocol located in
``three_dfs.import_plugins`` and register themselves either by calling
``three_dfs.import_plugins.register_plugin`` or by exposing an entry
point in the ``three_dfs.import_plugins`` group. Each plugin provides two
methods:

* ``can_handle(source: str) -> bool`` identifies whether a plugin can
  process the requested identifier.
* ``fetch(source: str, destination: Path) -> dict[str, Any]`` downloads
  the asset into the managed ``destination`` path and returns metadata.

When ``three_dfs.importer.import_asset`` receives a string that does not
resolve to a local file, it evaluates the registered plugins. The first
plugin whose ``can_handle`` method returns ``True`` is invoked to obtain
the managed asset. Plugins can provide additional metadata such as the
remote URL, author attribution, or authentication details; the importer
merges this metadata into the stored asset record and automatically
records the plugin identifier. Plugins should write their downloaded
asset to the supplied destination path and may include an ``extension``
field in the returned metadata when the original identifier does not
include a file suffix.

### Plugin scaffolding

The ``three_dfs.import_plugins.scaffold_plugin`` helper generates a
starter module with placeholders for authentication, scraping, and
metadata mapping logic:

```python
from pathlib import Path
from three_dfs.import_plugins import scaffold_plugin

plugin_path = scaffold_plugin("Sketchfab", Path("./plugins"))
print(f"Plugin scaffold written to {plugin_path}")
```

The generated module registers the plugin automatically; developers only
need to fill in the TODO hooks before packaging the plugin via an entry
point.
=======
The importer can fetch remote assets through pluggable backends. Plugins
implement the :class:`three_dfs.import_plugins.ImportPlugin` protocol and
register themselves either programmatically via
``three_dfs.import_plugins.register_plugin`` or by exposing an entry point in
the ``three_dfs.import_plugins`` group. When the importer receives an
identifier that does not resolve to a local file it asks the registered
plugins if they can handle the source and delegates the download to the
matching implementation. The returned metadata is merged with the built-in
fields so remote associations (for example ``remote_source``) are preserved in
the resulting asset record.

To jump-start development of a new integration you can use the scaffold
utility:

```python
from three_dfs.import_plugins import scaffold_plugin

module_path = scaffold_plugin("Sketchfab", "./plugins")
print(f"Created plugin skeleton at {module_path}")
```

The generated module contains TODO markers for authentication, scraping, and
metadata mapping. Fill in those hooks, ensure the plugin returns the fetched
asset in a supported format, and register it with the global registry.

