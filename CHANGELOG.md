# Changelog

All notable changes to qgis-mcp-workflows are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## v1.6.0 — 2026-08-31 — v0.3 roadmap salvage

Closes the two items the v0.3 roadmap flagged and v1.x never shipped. With these
done the roadmap doc holds nothing DESIGN.md doesn't already carry.

### Fixed: the plugin was broken on Python 3.9 (i.e. on macOS)

`_convert_to_python_type` and `_convert_attribute` used
`isinstance(value, int | float | str | bool | type(None))`. Runtime type unions
are 3.10+, so under the Python 3.9 QGIS-LTR bundles on macOS this raised
`TypeError: unsupported operand type(s) for |` on *every* call that converted an
attribute — `get_layer_features` was simply unusable there. Pre-existing on main
and found while testing the WKT work.

Neither `compileall` nor ruff catches this: it is valid syntax that fails only
when executed. `tests/test_macos_support.py` now walks the plugin's AST for
runtime unions in `isinstance`/`issubclass`; mutation-verified.

### Added: real WKT geometry

`get_layer_features(geometry_format="wkt")` returns actual geometry for polygons
and lines, not just the `wkt_summary` point count. `geometry_precision` and
`simplify_tolerance` control size — measured on a 400-vertex ring: 401 vertices /
8.7 KB at tolerance 0, 26 vertices at 0.001, 5 at 0.01. The default stays
`"summary"`, so existing callers are unaffected.

### Added: qgis_render_from_duckdb (19 tools total)

Queries a DuckDB file and renders the result with no CSV intermediate. Geometry
via `geometry_column` (WKT text) or `lon_column`/`lat_column`.

The connection is opened **read-only** and the query is wrapped in a LIMIT, so a
mistaken `SELECT *` against a multi-GB database can neither mutate it nor pull it
into memory; `row_limit_hit` reports truncation rather than hiding it. New
plugin primitive `render_wkt_features` does the rendering and is
transport-agnostic — anything holding WKT can use it.

New optional extra: `duckdb`.

**Verification caveat, stated plainly:** the real target, PFLOW's
`viz/pflow.duckdb` (~8.8 GB), lives on the author's Windows machine and was not
reachable from here. Everything was verified against a synthetic DuckDB built to
exercise the same code paths — WKT polygons, lon/lat points, graduated styling,
the row cap, read-only enforcement and every error path — but not against the
real schema. The first run against `pflow.duckdb` should be treated as the real
test, and column names will need to match that schema.

231 passed, 2 skipped.

## v1.5.0 — 2026-08-31 — QuickMapServices basemaps + preset repair

### Fixed: three basemap presets silently rendered watermarked tiles

`positron`, `dark_matter` and `voyager` pointed at `basemaps.cartocdn.com`, which
CARTO has since put behind an API key. Nothing detected it — the CDN answers HTTP
200 with a well-formed PNG reading "API KEY REQUIRED" in every tile, so
`isValid()` passes and the response reports a live basemap. Figures looked
plausible and encoded a watermark.

Presets are now named for **role** rather than vendor — `light`, `dark`,
`streets`, `imagery` — because the vendor changing under us is exactly the
failure mode. Old names remain accepted as aliases, and the response reports the
canonical name so it says what was actually drawn.

- `light` → Esri World_Light_Gray_Base, `dark` → Esri World_Dark_Gray_Base
- `streets` → OpenStreetMap (where `voyager` and `osm` now resolve)
- `imagery` → Esri World_Imagery

`voyager` had no keyless like-for-like replacement, so it resolves to the nearest
honest equivalent rather than an Esri service pretending to be it. Attribution
strings are copied verbatim from each provider's own metadata (the ArcGIS REST
`copyrightText` field), which also corrects the imagery credit — it named Maxar
after Esri had switched the source to Vantor.

### Added: QuickMapServices as a basemap source

`basemap="qms:<id>"` draws any usable source from the QuickMapServices catalog
installed in the QGIS profile — 55 of ~100 on a stock install, against the 4
built-in presets. QMS need not be loaded or even enabled; only present on disk,
so it works in headless mode.

- `qgis_mcp_workflows_plugin/quickmapservices.py` — the single implementation.
  Nothing else parses QMS INIs.
- `qgis_list_basemaps` (new tool, 18 total) — presets plus the catalog, filterable
  by `group` and `keyless_only`, with `qms_rejected` explaining every exclusion.

Resolution happens **plugin-side**, not in the MCP server: the catalog lives in
the QGIS user profile, which exists wherever QGIS runs and not necessarily on the
machine running the server — under `--transport=plugin` against another host,
MCP-side resolution would read the wrong profile or none.

Sources are filtered to what a bare `type=xyz` provider URI can honestly draw,
and each rejection is reported with a reason rather than silently omitted:
non-EPSG:3857 sources (they would draw misregistered — plausible and wrong),
MVT/WMS entries that need a different provider, and providers whose terms
restrict tile access to their own apps. Resolution mirrors QMS's own loader:
zoom defaults, the `{y}`→`{-y}` rewrite for bottom-origin schemes, and the
`=`/`&` percent-escaping without which any URL carrying a query string is
corrupted.

Unknown ids suggest near matches. `BasemapNotFoundError` is new.

### On detecting a dead basemap at runtime: not possible

Documented at `_load_basemap_layer` rather than left to be re-derived. A
key-walled tile is a valid 200 PNG; no status code or QGIS call separates it from
a real one. Only the pixels do. `tests/test_basemap_liveness.py` (`pytest -m
network`, deselected by default) fetches a real tile per preset and asserts
colour complexity — 21-24 distinct colours for the watermarked CARTO tile against
179-724 for working services. Mutation-verified. Sample dense urban tiles only: a
uniform tile reads as broken on every provider, working or not.

Also clears the two long-standing RUF001 lint errors, so `ruff check src/ tests/`
is clean.

202 passed, 3 skipped; 8 network tests pass live.

## v1.4.1 — 2026-08-31 — macOS support

The server and both transports now run on macOS. Previously the headless
transport resolved its launcher to `sys.executable` (the uv venv's Python 3.12,
which has no PyQGIS), so headless mode could not start at all on a Mac.

Added:
- macOS launcher auto-detection in `HeadlessExecutor`: `/Applications/QGIS-LTR.app`,
  then `QGIS.app`, then any `/Applications/QGIS*.app`, resolving to
  `Contents/MacOS/bin/python3`. Homebrew's `python3` is deliberately skipped — no PyQGIS.
- `HeadlessExecutor._bundle_env()` — derives `PROJ_LIB`, `GDAL_DATA` and
  `QGIS_PREFIX_PATH` from the launcher's `.app` bundle and injects them into the
  subprocess. Values already set in the environment are left alone.
- `tests/test_macos_support.py` (10 tests) — launcher precedence, env derivation,
  user-override precedence, non-bundle launchers, and a guard against Python
  3.11+ constructs in the plugin package.

Fixed:
- **Graduated renders silently produced one flat colour for every class.** QGIS
  derives the user profile from Qt's organization/application name; unset, it
  resolved to `~/Library/Application Support/profiles/default` instead of
  `.../QGIS/QGIS3/profiles/default`, so `QgsStyle.defaultStyle()` loaded zero
  color ramps and the palette lookup fell through. `headless_runner.py` now sets
  the same org/app names QGIS Desktop uses, before constructing `QgsApplication`.
- **Every CRS came back invalid under headless on macOS.** Without `PROJ_LIB`,
  PROJ cannot open `proj.db` — `QgsCoordinateReferenceSystem("EPSG:4326").isValid()`
  returned `False`, so renders reprojected wrong rather than failing loudly.
- `qgis_mcp_workflows_plugin/plugin.py` used `datetime.UTC` (Python 3.11+), which
  raised `ImportError` under the Python 3.9 that QGIS-LTR bundles on macOS. Now
  `timezone.utc`. The plugin package runs in QGIS's interpreter, so it is pinned
  to the oldest Python any supported QGIS ships.
- `.mcp.json` invoked `uv run --no-sync src/qgis_mcp_workflows/server.py` — a bare
  venv plus `--no-sync` plus a script path meant `ModuleNotFoundError: No module
  named 'mcp'` and the client saw the connection close. Now uses the
  `qgis-mcp-workflows-server` console entrypoint and syncs.
- Server startup banner logged a hardcoded `v1.3.0` while `pyproject.toml` said
  `1.4.0`. Now read from installed package metadata.
- `.gitattributes` added (`* text=auto eol=lf`) so a Windows checkout cannot flip
  the repo to CRLF. `main` is clean, but the unmerged
  `feat/choropleth-diverging-colormaps` branch shows the failure mode: 72 tracked
  files rewritten, a small feature commit turned into a 20k-line diff, and
  `.python-version` — which uv reads to pick an interpreter — among the casualties.

Verified on QGIS-LTR 3.40.5 (Apple Silicon): headless choropleth render with a
correct YlOrRd graduated ramp, `EPSG:4326` and `EPSG:6677` both resolving.

## v1.3.0 — 2026-05-22 — Vault integration (v0.6 milestone)

Closes the v0.6 milestone from DESIGN.md §7. This codebase now feeds the obsidian-vault knowledge system end-to-end.

Added:
- `scripts/weekly_figures.py` — renders the weekly figure set (`choropleth`, `trajectory`, optional `link_density`) into `H:/Dropbox/obsidian-vault/wiki/qgis/figures/weekly/<date>/` + writes a `manifest.json`.
- `docs/vault-integration.md` — terse README pointing at the `/kb-*` workflow.
- `tests/test_weekly_figures.py` (3 tests).

Vault-side changes (separate commits in `obsidian-vault`):
- `wiki/qgis/index.md` updated for the rename, refreshed concept-page links.
- `wiki/qgis/{tool-surface,transports,big-data-discipline,error-taxonomy,zone-id-systems,compound-mode,drm-network}.md` produced by `/kb-compile qgis` from `raw/qgis/dev/`.
- `wiki/qgis/src-{overview,architecture,recent-changes}.md` source summaries.
- `wiki/pflow/applications-qgis.md`, `wiki/gufm/applications-qgis.md` — Tobler-bridge cross-links.
- First `reports/qgis/v1.3-ship-readout.md` produced.

Vault skill registry updated: `kb-ingest`, `kb-compile`, and `kb-report` now accept `qgis` as a valid project slug (previously rejected — the vault was pre-seeded for `qgis` but the skills hadn't been opened up to it yet).

Scheduling: weekly run at `0 9 * * 1` (Monday 09:00 local) — configurable via `/schedule`. Documented in `docs/vault-integration.md`; first scheduled run is manual until the user opts in.

Unchanged: tool surface (14 tools), response shapes, error taxonomy. No runtime dependency changes; the weekly script uses only the existing MCP tools.

## v1.2.0 — 2026-05-22 — qgis_render_link_density

New tool for DRM-link traffic-density choropleths from PFLOW trajectories.

Added:
- `qgis_render_link_density(trajectory_csvs, drm_network_path, output_png, ...)` — streams trajectory CSVs row-by-row, aggregates per-link counts/sums, renders graduated line layer. Big-data ready (works on multi-GB inputs without loading them fully).
- `scripts/build_drm_network.py` — one-time prep script. Reads `drm_*.tsv` (47 prefecture shards), writes `assets/drm_network.gpkg` indexed by `link_id`. Requires `[drm]` extra (`pyogrio` + `geopandas`).
- `LinkDensityResult` pydantic model. `DRMNetworkNotFoundError` exception.
- `[drm]` optional extra (`pyogrio>=0.7`, `geopandas>=0.14`) — used only by the prep script; tool runtime has no new deps.
- Plugin handler `render_link_density` (graduated line symbology over the DRM GeoPackage). Uses existing `QgsGraduatedSymbolRenderer(field)` + `_CLASSIFICATION_METHODS` pattern for consistency with `render_choropleth`.
- W17 demo `--with-link-density` flag (`scripts/demo_w17.py`).
- Unit tests: `tests/test_render_link_density.py` (7), `tests/test_link_density_aggregate.py` (6), `tests/test_build_drm_network.py` (5), `tests/test_errors_drm.py` (1). Slow integration: `tests/test_build_drm_network_gpkg.py` (2).
- `pythonpath = ["."]` added to `[tool.pytest.ini_options]` so `from scripts.build_drm_network import ...` works in test discovery.

Unchanged:
- Existing 13 tools, response shapes, error taxonomy, transports.
- No new runtime deps for `qgis-mcp-workflows` itself — pyogrio + geopandas are only the prep-script's deps.

Resolves DESIGN.md §8 open question #8.

## v1.1.0 — 2026-05-22 — Rename to qgis-mcp-workflows

Pure rename release. The package, plugin, console script, env vars, and MCP-client config key all change to make the fork's positioning ("workflow tools, not 51 PyQGIS primitives") the literal name. No behavior changes; 99-test suite green before and after.

Renames:
- Package: `qgis-mcp-north` → `qgis-mcp-workflows`
- Plugin folder: `qgis_mcp_north_plugin/` → `qgis_mcp_workflows_plugin/`
- Console script: `qgis-mcp-north-server` → `qgis-mcp-workflows-server`
- Python package: `qgis_mcp_north` → `qgis_mcp_workflows`
- Env vars: `QGIS_MCP_NORTH_*` → `QGIS_MCP_WORKFLOWS_*` (HOST, PORT, TRANSPORT, TOOL_MODE, QGIS_LAUNCHER, REPO_ROOT, LOG_FILE, LOG_LEVEL)
- MCP-client config key in installer: `qgis-north` → `qgis-workflows`
- Error base class: `QgisMcpNorthError` → `QgisMcpWorkflowsError`
- Logger: `QgisMcpNorthServer` → `QgisMcpWorkflowsServer`
- Plugin LOG_TAG: `MCP-NORTH` → `MCP-WORKFLOWS`

Unchanged:
- Socket port `9877` (still distinct from upstream's 9876)
- Co-existence guarantee with upstream `nkarasiak/qgis-mcp`
- Plugin class name `QgisMCPServer` (internal; inherited from upstream)
- Tool names (`qgis_layer_inspect`, `qgis_render_choropleth`, etc.)
- Tool response shapes, error message format, escape-hatch behavior
- Historical completion-report docs (`docs/v0.3-*`, `docs/v0.4-*`, `docs/v0.5-*`, `docs/v1.0-*`) — frozen snapshots

Migration: see [README §v1.1.0 rename migration](README.md#v110-rename-migration).

Latent-bug fix folded in: `src/qgis_mcp_workflows/helpers.py:38`'s `importlib.metadata.version("qgis-mcp")` (looked up upstream's package name) is now `version("qgis-mcp-workflows")` — pre-existing diagnose mismatch.

## [1.0.0] — 2026-05-14

### Added
- **Tool surface complete.** Final 3 stubs shipped:
  - `qgis_style_categorized` — categorical (one color per unique value) symbology with per-class feature counts.
  - `qgis_style_graduated` — graduated (value-binned) symbology with `mode ∈ {quantile, equal_interval, natural_breaks, pretty}` and explicit breaks array.
  - `qgis_eval` — arbitrary PyQGIS escape hatch with `return_vars` capture (unbound names omitted; complex types repr-ified via `_json_safe`).
- **Compound mode.** `QGIS_MCP_NORTH_TOOL_MODE=compound` env var collapses 13 standalone tools to 5 grouped tools (`qgis_inspect`, `qgis_style`, `qgis_render`, `qgis_export`, `qgis_eval`) for token-constrained LLMs.
- **Benchmarks scaffolding.** `tests/benchmarks/` with cold-start, trajectory-scaling (1k/10k/100k/500k), choropleth, and transport-parity benchmarks. Opt-in via `pytest -m bench`. `[bench]` extra adds `pytest-benchmark>=4.0`.
- **End-to-end W17 demo.** `scripts/demo_w17.py` runnable standalone, plus `tests/integration/test_w17_demo.py` with fake / plugin / headless modes.
- **Installer tests.** 13 tests covering `qgis_plugins_dir`, `install_plugin`, `uninstall_plugin`, `configure_client`, and CLI arg parsing.
- **Docs.** `README.md` full rewrite (no longer describes upstream's 51-tool surface). New: `docs/v0.5-completion-report.md`, `docs/v1.0-completion-report.md`, `docs/pflow-usage.md`, `docs/benchmarks-v0.5.md`, `CHANGELOG.md`. `CLAUDE.md` updated to reflect 13/13 tools + compound mode.
- **134-zone synthetic fixture** at `tests/benchmarks/fixtures/scaled_zones_134.geojson` (built by `scripts/build_scaled_zones.py`).
- **`assets/screenshots/w17_demo.png`** — produced by the W17 demo; embedded in README.

### Changed
- `qgis_mcp_north_plugin/plugin.py`:
  - `set_layer_style` now returns rich response (`n_classes`, `classes:[{value, color, n_features}]`, plus `breaks` + `mode` for graduated). Honors `mode` arg via existing `_CLASSIFICATION_METHODS` dict.
  - `execute_code` accepts `return_vars` + uses new `_json_safe()` helper.
  - Both handlers gracefully degrade when running in headless transport (no `iface.layerTreeView()` call).
- `install.py` — added missing `main()` invocation under `if __name__ == "__main__":` (caught by the new test suite).

### Tested
- 99 unit tests pass + 3 skips (movingpandas, plugin E2E, headless E2E — gated on tooling availability) + 11 deselected (benchmarks).
- Ruff clean across `src/`, `tests/`, `scripts/`, `install.py`.

### Support claim
- **Windows-only for v1.0.** Linux/macOS may work via PyQGIS-on-PATH but is unverified. Refinement deferred until a non-Windows user reports.

### Out of scope (deferred to v2+)
- DuckDB integration (`qgis_render_from_duckdb`).
- DRM-link aggregation (`qgis_render_link_density`).
- WMS/WFS map servers.
- deck.gl JSON-spec output.

## [0.5.0] — 2026-05-14

### Added
- 5 of 8 stubbed v0.5 tools shipped:
  - `qgis_render_trajectory` — lines/points/heatmap from PFLOW CSV (or GPX). Stride sampling + `max_points` ceiling. Optional `movingpandas` speed-binned line rendering via `[trajectory]` extra.
  - `qgis_render_od_flows` — centroid arcs over a zones layer with data-defined stroke width. Unmatched origin/destination counts surface in response.
  - `qgis_project_load` — loads `.qgz`/`.qgs`, returns layers + layouts; stateful for downstream tools.
  - `qgis_export_layout` — PNG/PDF/SVG via `QgsLayoutExporter`. Loads `qgz_path` internally if not already loaded.
  - `qgis_batch_render` — fan-out per attribute value. Active-layer convention (saved-active → first vector fallback). Manifest + per-value errors. `subset_string` reset in `finally`.
- New typed errors: `EmptyAfterFilterError`, `ProjectLoadError`, `LayoutNotFoundError` — each ending in "Next: …" recovery hints.
- 37 new unit tests (66 total).
- Synthetic test fixtures: `tests/fixtures/tiny_trajectory.csv` (30 rows × 3 trips), `tiny_od.csv` (6 OD pairs), `tiny_zones.geojson` (4 polygons).

### Changed
- All 5 new plugin handlers ship with both plugin AND headless transports for free, per v0.4's stub-iface architecture.

## [0.4.0] — 2026-05-01

### Added
- Headless transport. PyQGIS subprocess executor (`src/qgis_mcp_north/executors/headless.py`) re-uses the plugin's `QgisMCPServer.execute_command` via a stub `iface`.
- `--transport={plugin,headless,auto}` CLI flag with auto-probe of port 9877.
- `qgis_load_layer(crs=...)` wired through plugin's `set_layer_crs` with rollback on failure.
- New errors: `HeadlessUnavailableError`, `CrsMismatchError`.
- 4 new integration tests for headless executor.

## [0.3.0] — 2026-04-30

### Added
- v0.3 MVP — 5 of 13 MCP tools implemented end-to-end against the plugin transport:
  - `qgis_layer_inspect`, `qgis_load_layer`, `qgis_render_map`, `qgis_render_choropleth`, `qgis_figures_to_pptx`.
- Plugin: `render_layers_to_path` and `render_choropleth` (atomic load+style+render+cleanup).
- Executor abstraction at `src/qgis_mcp_north/executors/plugin.py`.
- Typed errors module with 5 initial classes.
- 25 unit tests.

## [0.1.0] — 2026-04-30

### Added
- Initial fork from `nkarasiak/qgis-mcp` v0.2.1.
- Cut to 12 workflow tools + 1 escape hatch (`qgis_eval`) — see `docs/DESIGN.md`.
- Renamed: package `qgis_mcp_north`, plugin folder `qgis_mcp_north_plugin`, default port 9877 (vs upstream 9876).
- 13 tool stubs registered with FastMCP.
