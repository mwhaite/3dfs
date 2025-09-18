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
