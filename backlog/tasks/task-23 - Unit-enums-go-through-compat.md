---
id: TASK-23
title: Unit enums go through compat
status: Done
assignee: []
created_date: '2026-11-10 06:00'
labels:
  - bug
  - qgis4
dependencies:
  - TASK-22
priority: medium
ordinal: 23000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Parked MEDIUM from #38's review: `plugin.py` named the unscoped `QgsUnitTypes.LayoutMillimeters` in `compose_layout` and the TASK-22 panel code (4 sites). QGIS 3.30+ has `Qgis.LayoutUnit.Millimeters`; under QGIS 4 / PyQt6 the old alias may be gone, which would break `qgis_compose_layout` and `qgis_compose_panels`. The inventory found two more unit-enum sites of the same family: `QgsUnitTypes.RenderMillimeters` in `render_diagram_map` (inside try/except, so a missing alias would pass silently) and the heatmap radius unit `Qgis.RenderUnit.Pixels if hasattr(Qgis, "RenderUnit") else 1`, whose fallback `1` is `RenderMapUnits`, not pixels (`RenderPixels` is 2 on QGIS 3.40 / 3.42). That fallback runs on QGIS 3.28-3.29, which the plugin still supports.

Plan: `LAYOUT_MM`, `RENDER_MM`, `RENDER_PIXELS` in `qgis_mcp_workflows_plugin/compat.py` with the try-new-then-old pattern; `plugin.py` imports them and stops naming `QgsUnitTypes`. Tests first: an AST tripwire (no unit enum named outside compat.py) and a compat test that loads compat.py against stub QGIS modules shaped like QGIS 3.28 (old aliases only) and QGIS 4 (new enums only).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 compat.py defines LAYOUT_MM, RENDER_MM and RENDER_PIXELS, new form first, old form as fallback
- [x] #2 No plugin file other than compat.py names `QgsUnitTypes.*`, `Qgis.LayoutUnit` or `Qgis.RenderUnit` (AST tripwire test)
- [x] #3 compat imports and picks the right values on a QGIS-3.28-shaped and a QGIS-4-shaped stub
- [x] #4 The heatmap radius unit is pixels on every supported QGIS (fallback is RenderPixels, not 1)
- [x] #5 Full suite (live tests on local QGIS 3 headless), ruff 0.16.5, plugin parses as Python 3.9; QGIS 4 run or stated unverified
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
The brief said about 8 `LayoutMillimeters` sites; there were 4 (compose_layout, `_panel_label`, `_panel_colorbar`, compose_panels). Probe on all three local builds (OSGeo4W 3.40.5 and 3.42.1, QGIS LTR 3.40.14, all Qt5): old and new spellings are the same object, so on QGIS 3 the swap changes nothing. No QGIS 4 / Qt6 build on this PC (no QGIS4 profile), so QGIS 4 stays unverified; the stub test models it. The plugin did load under QGIS 4.2.2 in TASK-4 with `QgsUnitTypes` imported, so the class (the fallback source) exists there.

Evidence (win, 2026-11-10): RED 3 failed (2 compat, tripwire listing the 6 sites), then GREEN. Full suite 742 passed, 3 skipped, 1 failed (the known Windows file lock in test_headless_executor.py::test_add_vector_layer_returns_layer_id, same on main per doc-2); after the version bump, docs/compat/live layout tests 80 passed, 1 skipped (AGENTS.md absent). Live headless probe: compat constants equal the scoped enums, heatmap render 4.8 KB PNG. ruff@0.16.5 src/ tests/ clean (plugin folder: 73 findings, identical to main). Plugin parses with feature_version=(3, 9).

Review (code-reviewer and python-reviewer, fresh context, read-only): both APPROVE, no CRITICAL/HIGH/MEDIUM. Fixed: metadata.txt changelog no longer implies QGIS 4 was run; CLAUDE.md says the tripwire covers unit enums only; DESIGN status v1.16.1. Parked LOW: the tripwire misses `from qgis.core import QgsUnitTypes as U` aliasing and `getattr(Qgis, "RenderUnit")`; `_Stub` answers `hasattr` for dunders; no stub for a half-migrated build (RenderUnit without Pixels: both fall back together, safe); `try/except Exception: pass` around the diagram `sizeType` (pre-existing) could be narrowed. Cross-harness review not run (no agy, agy_run.py absent from the vault checkout, Codex unusable on this account): North decides.

Coordinated with the sibling session on the other parked #38 MEDIUM (CSV keys off the layer): it took TASK-24 / v1.16.2; the second PR to merge resolves version lines, CHANGELOG, metadata changelog and uv.lock.
<!-- SECTION:NOTES:END -->
