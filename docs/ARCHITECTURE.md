# 3dfs Architecture

This diagram shows the core runtime flow, data stores, and key modules.

```mermaid
flowchart TD
  subgraph Entry
    CLI[CLI: three-dfs] --> APP[App bootstrap]
    MOD[Python: -m three_dfs] --> APP
  end

  subgraph UI[UI (PySide6)]
    SIDEBAR[Tag Sidebar]
    PREVIEW[Preview Pane]
    ASSEMBLY[Assembly Pane]
    IMPORT[Import Dialog]
  end
  APP --> UI

  subgraph Importer
    IMP[Importer]
    REG[Plugin Registry]
    P1[Plugin A]
    P2[Plugin B]
  end
  IMPORT -->|import request| IMP
  IMP -->|local file?| ASSETS[(Managed Assets)]
  IMP --> REG
  REG --> P1 & P2
  P1 -->|can_handle + fetch| TMP[(Downloaded File)]
  P2 -->|can_handle + fetch| TMP
  TMP -->|write to| ASSETS
  IMP -->|metadata merge| SVC

  subgraph Storage
    SVC[Asset Service]
    REPO[Repository]
    DB[(SQLite: assets, tags, customizations, relationships)]
  end
  UI -->|query| SVC
  SVC --> REPO --> DB
  SVC -->|records| UI

  subgraph Thumbnails
    TH[Thumbnail Generator]
    TC[(Thumbnails Cache)]
  end
  PREVIEW -->|needs image| TH
  TH -->|read| ASSETS
  TH -->|write| TC

  WATCHER[Filesystem Watcher]
  APP --> WATCHER
  WATCHER --> ASSEMBLY

  subgraph Customizer
    DIALOG[Customizer Panel]
    PIPE[Execution Pipeline]
  end
  PREVIEW -->|Customize…| DIALOG
  DIALOG -->|plan + run| PIPE
  PIPE -->|persist derivatives| SVC
  PIPE -->|copy artifacts| ASSETS

  subgraph Filesystem
    ASSETS
    TC
  end

  SIDEBAR -->|tag ops| SVC
  ASSEMBLY -->|component metadata| SVC
```

Notes
- Entrypoints: `three-dfs` and `python -m three_dfs` initialize the PySide6 shell, asset services, and filesystem watchers.
- Importer: resolves local files or delegates to plugins (`can_handle`, `fetch`), then merges returned metadata into managed assets.
- Storage: the asset service wraps the repository layer which persists records directly in SQLite (assets, tags, customizations, relationships).
- Thumbnails: generated on demand from assets and cached on disk for fast redraws.
- Customizer: the preview pane launches the embedded panel which runs the execution pipeline and records derivative assets.
- Assemblies: the app watches assembly folders for changes so the pane can refresh components and arrangement scripts automatically.
- Data: managed assets and thumbnails live under the configured library root; metadata and tags reside in `~/.3dfs/assets.sqlite3`.

Local commands
- Setup: `source setup.sh` (activates) or `./setup.sh --activate`
- Lint: `hatch run lint`
- Test: `hatch run test`
- Run: `hatch run three-dfs`
