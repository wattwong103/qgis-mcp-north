# Design: agent-reliability-v1.7

## Health tools

Plugin handlers `ping` and `diagnose` already exist. MCP tools always register (`@mcp.tool`, not `_maybe_tool`) so they work in full and compound mode.

- `qgis_ping` → `{pong, transport}` where `transport` is `plugin` / `headless` / the FakeExecutor class stem.
- `qgis_diagnose` → plugin `diagnose` then `helpers.enrich_diagnose` (server vs plugin version). `PluginUnavailableError` unchanged.

## SDK pin and import guard

`pyproject.toml`: `mcp[cli]>=1.20.0,<3`. `server.py` tries `mcp.server.fastmcp.FastMCP`, then `mcp.server.mcpserver.MCPServer as FastMCP`. Any other import failure prints one stderr line pointing at `uv sync` (Dropbox-hollow `.venv`) and re-raises.

## Clients

One launch argv builder. CLI clients invoke `<cli> mcp add … qgis-workflows -- <argv>`. Uninstall uses the same name. Grok with no CLI merges `[mcp_servers.qgis-workflows]` into `~/.grok/config.toml`. Claude Code with no CLI updates repo `.mcp.json` and prints the working command. Cursor/vscode/windsurf/zed unchanged.

## Compound catch-up

`qgis_inspect(kind="basemaps")`, `qgis_render` modes `link_density` / `diagram_map` / `catchment` / `duckdb`, `qgis_export(kind="compose_layout")`, plus `diverging`/`center`/`basemap`/`arc_style`/`label_field` forwarded to the standalone tools. Wrappers do not reimplement logic.

## PNG preview

`helpers.maybe_preview(result)`: if `output_path` is an existing `.png` ≤ 1.5 MB, return `[TextContent(json), ImageContent]`; otherwise return the Pydantic model so FakeExecutor tests keep working. Applied via `@with_png_preview` under `@_maybe_tool` on render/export tools that write a single file. No new dependency; no resampling.
