# Proposal: agent-reliability-v1.7

## Why

The QGIS plugin on :9877 can be healthy while every MCP client reports a dead server: the stdio process dies on import, `install.py` does not actually configure Claude Code / Codex / Grok, and there is no `ping`/`diagnose` tool to tell those two failures apart. Upstream already solved the health-check and CLI-install pieces; we take those, not their 118-tool surface.

## What changes

- Recreate a Dropbox-corrupted `.venv` is an operator step; the code grows an import-time stderr hint (`uv sync`) and pins `mcp[cli]>=1.20.0,<3` with a FastMCP / MCPServer import fallback.
- Expose plugin `ping` / `diagnose` as `qgis_ping` / `qgis_diagnose` (always registered, both tool modes), wrapping `helpers.enrich_diagnose`.
- `install.py` actually installs **Claude Desktop** (JSON, already works), **Claude Code** (`claude mcp add -s user`), **Codex** (`codex mcp add`), and **Grok** (`grok mcp add`, TOML fallback). Server name `qgis-workflows`. Launch command is always `uv run --directory <repo> qgis-mcp-workflows-server`.
- Compound mode gains the post-v1.0 tools/params it currently hides.
- Render/export PNG tools attach MCP `ImageContent` when the written file exists (path remains canonical).
- Docs fence covers AGENTS.md; version 1.6.0 → 1.7.0.

## Out of scope

Processing toolbox, feature editing, `qgis_assign_section_load`, pptx layout fidelity, kimi/gemini/qwen/hermes clients, mcp 2.x rewrite.
