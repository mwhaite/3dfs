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
    SVC[Storage Service]
    REPO[Repository]
    SA[SQLAlchemy Session]
    DB[(SQLite: assets, tags, asset_tags)]
  end
  UI -->|query| SVC
  SVC --> REPO --> SA --> DB
  SVC -->|records| UI

  subgraph Thumbnails
    TH[Thumbnail Generator]
    TC[(Thumbnails Cache)]
  end
  PREVIEW -->|needs image| TH
  TH -->|read| ASSETS
  TH -->|write| TC

  subgraph Filesystem
    ASSETS
    TC
  end

  SIDEBAR -->|tag ops| SVC
```

Notes
- Entrypoints: `three-dfs` and `python -m three_dfs` initialize UI and services.
- Importer: resolves local files or delegates to plugins (`can_handle`, `fetch`), then merges metadata.
- Storage: service/repository wrap SQLAlchemy models and handle transactions.
- Thumbnails: generate on-demand from assets; cache writes to filesystem.
- Data: SQLite tables (`assets`, `tags`, `asset_tags`); files under managed asset and thumbnail paths.

Local commands
- Setup: `source setup.sh` (activates) or `./setup.sh --activate`
- Lint: `hatch run lint`
- Test: `hatch run test`
- Run: `hatch run three-dfs`
