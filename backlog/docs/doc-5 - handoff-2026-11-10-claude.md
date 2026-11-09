---
id: doc-5
title: handoff-2026-11-10-claude
type: other
created_date: '2026-11-10 18:00'
---


## Goal
New features after the 1.15.x fixes (North, 2026-10-30): "PFLOW camera-ready figure first, then mesh grid". TASK-22: make PFLOW's `fig_spatial` (2 × 2: simulated vs observed, origins and destinations, one scale per row) with the tool, with a colour bar, set fonts and set line weights.

## Done
- Design and plan (docs/superpowers/specs|plans/2026-10-30-panel-grid*), approved by North on 2026-11-10; built inline. One PR instead of two stacked PRs, at North's request.
- PR #38 (task/task-22-panel-grid, 1.16.0): new tool `qgis_compose_panels`.
  - Server: `panels.py` computes the scales and ticks; the tool reads the CSV and validates.
  - Plugin: `compose_panels` lays out panels, titles, rotated row labels and gradient colour bars.
  - Headless fonts: offscreen Qt sees only Open Sans; each headless process registers the OS fonts once.
- Reviews: fresh-context code, Python and security reviewers; no CRITICAL or HIGH. One fix pass, each fix with a RED→GREEN test:
  - default extent = the zones drawn;
  - Japanese labels headless;
  - CRS with no authid;
  - `PAGE_TOO_SMALL`;
  - a non-UTF-8 CSV and repeated keys are refused;
  - live layout-geometry test;
  - replay test.
- Suite: 740 passed, plus the 1 known Windows lock failure; ruff@0.16.5 clean; plugin parses as Python 3.9.
- Venv found empty at 04:39 (recreated by the MCP server's plain `uv run`; a macOS `bin/` dated 2026-11-05 shows the Dropbox-synced `.venv` mixing machines). Rebuilt offline with `uv sync --frozen --offline --all-extras` (72 packages).

## Files changed
- New: src/qgis_mcp_workflows/panels.py, tests/test_panels.py, tests/test_compose_panels.py, tests/test_panels_live.py.
- Changed: server.py (`PanelGroup`, `PanelsResult`, `qgis_compose_panels`); plugin.py (`compose_panels`, `_panel_layer`, `_panel_label`, `_panel_colorbar`); compat.py (enum shims, `font_families`, `is_offscreen`, `register_system_fonts`).
- Docs and versions: DESIGN, CLAUDE.md, README, CHANGELOG, version files, the TASK-22 file, this handoff.

## Verify next
- After #38 merges: pull main, then `uv sync --frozen --offline --all-extras` (venv to 1.16.0), then reconnect the qgis-workflows MCP server.
- PFLOW acceptance, a separate task in PFLOW:
  - `make_fig_spatial.py` writes the four share columns per szone, plus R², to a CSV;
  - render `fig_spatial.pdf` with `qgis_compose_panels` (`zones_path` = the szone shapefile, `join_field` = its key, `share_scale="row"`, `palettes=["Reds","Blues"]`, `tick_format=".1%"`, North's font and outline width);
  - compare it with the matplotlib figure.

## Open questions
- **Cross-harness review of #38 not run.** Codex is not usable on this account. On this machine `agy` is not on PATH, and `obsidian-vault/.claude/scripts/agy_run.py` is missing from the vault checkout (only a copy under `.git/loop-worktrees/`), because the vault sync is deferred: local edits overlap `wiki/log.md`. North decides: sync the vault and install agy here, run it elsewhere, or waive.
- **Root fix for the venv:** keep `.venv` out of Dropbox sync on both machines (e.g. Dropbox "ignored" attribute), so the Mac and this PC stop overwriting each other's interpreters.
- **Parked from #38:**
  - MEDIUM: CSV keys absent from the layer still enter the shared scale; the fix needs a layer-keys plugin command.
  - MEDIUM: `QgsUnitTypes.LayoutMillimeters` is unscoped here and in `compose_layout` (QGIS 4 unverified).
  - LOW items, listed in the PR body.
- **Floating ruff 0.17.4** flags `ledger.py:138` SIM102 (CI pins 0.16.5, clean).

## Owner next
- North: review and merge #38; decide the cross-harness route; the `.venv` sync exclusion.
- Claude, next session: the mesh-grid feature (North's second choice), which needs its own brainstorm and plan; or the PFLOW acceptance render once the CSV exists.

Status: DONE_WITH_CONCERNS (cross-harness review not run; two MEDIUM items parked with follow-ups proposed).
