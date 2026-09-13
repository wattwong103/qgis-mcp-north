# Spec: agent-reliability

## Requirements

### Health tools
- `qgis_ping` SHALL dispatch plugin `ping` and return `pong=true` plus the active transport name.
- `qgis_diagnose` SHALL dispatch plugin `diagnose`, run `enrich_diagnose`, and surface a version-match check.
- Both SHALL register in full and compound tool modes.

### SDK
- `mcp[cli]` SHALL be pinned `<3`.
- Import SHALL succeed on mcp 1.x FastMCP and mcp 2.x MCPServer.
- A failed SDK/pydantic import SHALL print a one-line `uv sync` hint to stderr.

### Installer
- `install.py --clients claude-desktop,claude-code,codex,grok` SHALL configure each at user scope under the server name `qgis-workflows`.
- Launch args SHALL contain `--directory` and `qgis-mcp-workflows-server`, never `src/qgis_mcp_workflows/server.py` when `uv` is present.
- Uninstall SHALL use `qgis-workflows`, not `qgis`.

### Compound mode
- Compound render/export/inspect SHALL dispatch the post-v1.0 standalone tools listed in DESIGN.md.

### Preview
- When a render/export tool writes an existing PNG under the size cap, the MCP result SHALL include `ImageContent`.
- `output_path` remains the canonical file contract.
