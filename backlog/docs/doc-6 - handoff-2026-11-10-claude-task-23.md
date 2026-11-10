---
id: doc-6
title: handoff-2026-11-10-claude-task-23
type: other
created_date: '2026-11-10 07:00'
---


## Goal
Parked MEDIUM from #38's review (North, 2026-11-10): `plugin.py` used the unscoped `QgsUnitTypes.LayoutMillimeters`, which QGIS 4 / PyQt6 may drop, breaking `qgis_compose_layout` and `qgis_compose_panels`. Route it through `compat.py` and check the other unscoped enums in the same code.

## Done
- TASK-23 (branch task/task-23-layout-unit-compat, 1.16.1):
  - `compat.py` gains `LAYOUT_MM`, `RENDER_MM` and `RENDER_PIXELS`: `Qgis.LayoutUnit` / `Qgis.RenderUnit` first (QGIS 3.30+), the `QgsUnitTypes` aliases as fallback (3.28).
  - `plugin.py` no longer imports `QgsUnitTypes`. The 4 layout sites, the diagram `sizeType` and the heatmap radius unit go through compat.
  - The heatmap fallback had been wrong: it passed `1`, which is `RenderMapUnits`. The 15-unit radius was in map units on QGIS 3.28 and 3.29.
- Tests:
  - an AST tripwire in tests/test_macos_support.py (no unit enum outside compat.py), sharing a walker with the Qt5 tripwire;
  - tests/test_compat_units.py loads compat.py against stub qgis modules shaped like QGIS 3.28 and like QGIS 4.
- Reviews: code-reviewer and python-reviewer, fresh context. Both APPROVE, with no CRITICAL, HIGH or MEDIUM. Doc LOWs fixed; test-strengthening LOWs parked (task file).
- Cross-harness review (North chose Codex): Codex APPROVED, with no CRITICAL, HIGH or MEDIUM. Its one LOW is already parked. Codex's shell can't run commands on this PC, so it reviewed from a stdin bundle (`codex exec -s read-only - < bundle`). The verdict is posted on PR #39.
- Probe: all three local QGIS builds (3.40.5, 3.42.1, 3.40.14; Qt5) have old and new spellings as the same object. No QGIS 4 install on this PC.
- Worktree venv rebuilt offline: `uv sync --frozen --offline --all-extras`.

## Files changed
- qgis_mcp_workflows_plugin/compat.py, qgis_mcp_workflows_plugin/plugin.py
- tests/test_macos_support.py, tests/test_compat_units.py (new)
- CHANGELOG.md, CLAUDE.md, docs/DESIGN.md (status), pyproject.toml, metadata.txt, uv.lock (1.16.1)
- backlog/tasks/task-23, this handoff

## Verify next
- On the Mac with QGIS 4.2.2: set `QGIS_MCP_WORKFLOWS_QGIS_LAUNCHER` to the bundle's `Contents/MacOS/python`. Then run `uv run --no-sync pytest tests/test_panels_live.py tests/test_output_folder_live.py tests/test_default_colour_live.py tests/test_palettes_live.py -q -rs`; they must run, not skip. That settles whether QGIS 4 still has the aliases (now irrelevant) and whether layout and diagram exports work there.
- After merge: `uv sync --frozen --offline --all-extras` in the main checkout (venv to 1.16.1), then reconnect the qgis-workflows MCP server.

## Open questions
- **QGIS 4 unverified.** The QGIS Desktop on this PC is OSGeo4W 3.40.5 on Qt 5.15.13, and it loads the main checkout's plugin, so only the Mac can settle QGIS 4 (Verify next).
- **CI's "plugin compiles on py3.9" job fails at setup:** `ubuntu-latest` (now Ubuntu 26.04) has no Python 3.9 in `actions/setup-python`. It is operator-owned (`.github/workflows/`). Proposed: `runs-on: ubuntu-24.04` for that job, or a `python:3.9-slim` container.
- **TASK-24 (sibling session, CSV keys off the layer, 1.16.2)** conflicts with this PR on version lines, CHANGELOG, the metadata.txt changelog and uv.lock. The PR merged second resolves them.

## Owner next
- North: review and merge; the CI runner pin.
- Claude, next session: the mesh-grid feature (needs its own brainstorm and plan), or the PFLOW acceptance render once its CSV exists.

Status: DONE_WITH_CONCERNS (QGIS 4 unverified: no QGIS 4 install on this PC; 5 test-hardening LOWs parked).
