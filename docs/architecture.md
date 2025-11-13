# 3dfs architecture

The diagram below shows the core runtime flow, data stores, and key modules.

```mermaid
flowchart TD
  subgraph Entry
    CLI[CLI: three-dfs] --> APP[App bootstrap]
    MOD[Python: -m three_dfs] --> APP
  end

  subgraph UI[UI (PySide6)]
    SIDEBAR[Tag Sidebar]
    PREVIEW[Preview Pane]
    PROJECT[Project Pane]
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
  WATCHER --> PROJECT

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

  PROJECT -->|component metadata| SVC
```

## Key observations

- **Entrypoints:** `three-dfs` and `python -m three_dfs` initialise the PySide6 shell, asset services, and filesystem watchers.
- **Importer:** resolves local files or delegates to plugins (`can_handle`, `fetch`), then merges returned metadata into managed assets. See [Extending 3dfs](extending.md#import-plugins) for the plugin protocol.
- **Storage:** the asset service wraps the repository layer and persists records in SQLite (`assets`, `tags`, `customizations`, `relationships`, `container_versions`).
- **Container versions:** each container can snapshot its metadata into `container_versions`, enabling version-aware linking and the UI’s dropdown selector.
- **Container linking:** metadata stores `links` (outgoing) and `linked_from` (incoming) entries, each optionally pinned to a version snapshot. Linked components imported from another container are persisted as `linked_component` records so refreshes keep the references intact.
- **Thumbnails:** generated on demand from assets and cached on disk for fast redraws.
- **Tag explorer:** the sidebar can launch a full-screen graph that visualises tag counts and co-occurrence via the asset service. Users click a tag to focus it and filter the repository.
- **Customizer:** the preview pane launches the embedded panel which runs the execution pipeline and records derivative assets. The [customizer documentation](customizer-backends.md) covers the backend protocol in detail.
- **Projects:** the app watches project folders for changes so the pane refreshes components and arrangement scripts automatically. Removal requests clean up attachment metadata before rescanning.
- **Data paths:** managed assets and thumbnails live under the configured library root. Legacy tag metadata remains in `~/.3dfs/assets.sqlite3` for forward compatibility.

For setup commands and troubleshooting tips, refer to the [getting started guide](getting-started.md) and [user guide](user-guide.md).
