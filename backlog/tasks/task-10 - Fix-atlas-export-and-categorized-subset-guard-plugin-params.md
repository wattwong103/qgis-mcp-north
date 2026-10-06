---
id: TASK-10
title: 'Fix atlas export and categorized subset; guard plugin params in tests'
status: In Progress
assignee: []
created_date: '2026-10-06 22:00'
labels:
  - bug
  - upstream-review
dependencies: []
priority: high
ordinal: 10000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-06 by the upstream comparison (nkarasiak/qgis-mcp v0.15.0 vs our fork point v0.2.1). Plugin handlers our tools call are wrong, and the FakeExecutor suite cannot see it because nothing checks that the params the MCP side sends are params the plugin handler takes.

1. `export_atlas`: the static `QgsLayoutExporter.exportToPdf(atlas, ...)` returns `(result, error)`. The handler compared the tuple to `LAYOUT_SUCCESS` and then failed formatting its own message, so every PDF atlas errored after the file was written. The PNG path's static export "failed" the same way and a fallback loop (missing `atlas.first()`) re-exported every page plus one extra.
2. `qgis_style_categorized(classes=...)` sends `classes_subset`; `set_layer_style` swallowed it in `**kwargs` and rendered every value.
3. The new guard also found `render_od_flows` receiving an unused `od_csv`, and `scripts/weekly_figures.py` demo mode dispatching `render_link_density` with the pre-v1.2 params (`trajectory_csvs`, no `density`), which a real plugin would reject with TypeError.

Dropped after checking: the review's zonal_stats NULL overcount and CSV encoding claims do not reproduce on QGIS 3.40 (NULL attributes come back as None; QGIS Python runs in UTF-8 mode).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 FakeExecutor refuses a dispatch whose params the plugin handler does not accept (named param, or a furniture key for handlers that forward kwargs), an unknown command, or a missing required param; the whole suite passes under the check
- [x] #2 PDF atlas export succeeds with one file; PNG atlas export writes each page once and lists exactly those files; a real export failure raises with the export code (live headless tests, red on the old plugin)
- [x] #3 classes_subset renders only the listed values in the given order, and the remaining features (and NULL) share one 'all other values' class reported last (live headless test)
- [x] #4 od_csv is no longer dispatched; weekly_figures demo mode dispatches the params render_link_density takes
- [x] #5 pytest tests/ green, ruff clean, DESIGN + CHANGELOG updated
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Files: tests/plugin_contract.py + tests/conftest.py (handler-signature check in FakeExecutor), tests/test_handler_param_parity.py, tests/test_plugin_live_fixes.py (requires_headless), qgis_mcp_workflows_plugin/plugin.py (set_layer_style, export_atlas), src/qgis_mcp_workflows/server.py (od_csv, classes description), scripts/weekly_figures.py, docs/DESIGN.md, CHANGELOG.md.
Validation: uv run --no-sync pytest tests/ -v; uv tool run ruff check src/ tests/; live headless tests on QGIS LTR 3.40 (M:).
<!-- SECTION:PLAN:END -->
