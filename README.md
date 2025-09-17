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
