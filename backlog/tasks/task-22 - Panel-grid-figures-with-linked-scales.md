---
id: TASK-22
title: Panel grid figures with linked scales (qgis_compose_panels)
status: Done
assignee: []
created_date: '2026-10-30 10:00'
labels:
  - feature
  - pflow
  - gufm
dependencies: []
priority: high
ordinal: 22000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A 2026-10-30 recon of GUFM and PFLOW found that their recent maps are hand-written matplotlib, chiefly because this tool draws one map per image. North chose the first target: the BigData 2026 camera-ready `fig_spatial` (2 × 2 sim vs observed, origins and destinations, shares on one scale per row, R² per row), made with the tool, with a colour bar, custom fonts and line weights. Mesh-grid maps come next (separate task).

Design: `docs/superpowers/specs/2026-10-30-panel-grid-design.md`. Plan: `docs/superpowers/plans/2026-10-30-panel-grid.md` (4 tasks, two stacked PRs). Large task: implementation waits for North's approval of the plan.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 `qgis_compose_panels` draws rows × cols panels of one polygon layer from CSV columns, sharing one extent
- [x] #2 Scales shared by row, column or all, capped at a percentile, reported in the result; one colour bar per scale group with formatted ticks (≥ on a capped top)
- [x] #3 Font family (missing ones reported), text sizes, zone outline width and colour, page size in mm; PDF/SVG vector, PNG at dpi
- [x] #4 Provenance sidecar and replay like every file-writing tool
- [x] #5 Docs, counts (28 tools), version 1.16.0; full suite and ruff
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Built inline from the plan on 2026-11-10 (one PR instead of two stacked, North's call). Rulings are in the PR body.

Review (fresh context, read-only): code-reviewer (whole branch), python-reviewer, security-reviewer. No CRITICAL or HIGH. Fixed in one pass, each with a test that failed first: the default extent framed the whole layer instead of the zones drawn (fig_spatial would have shown the wards small in the szone frame); headless Japanese labels drew as empty boxes (fonts now registered once per headless process); a zones CRS with no authid was read as EPSG:4326; a page too small for its panels exported garbage (now PAGE_TOO_SMALL); a non-UTF-8 CSV and repeated CSV keys are refused with Next hints; a live layout-geometry test now covers right and bottom bars and colorbar=False. Added a replay test (AC #4 end to end).

Parked: CSV keys absent from the layer still enter the shared scale (needs a layer-keys plugin command); QgsUnitTypes.LayoutMillimeters unscoped here and at 4 older sites (QGIS 4 unverified); tick_format width and PNG pixel area unbounded; label_size_pt description says it sizes the colour-bar label (it is tick_size_pt); font check is case-sensitive; join_field is placed in the memory-layer URI; LOCALAPPDATA unset makes a relative font glob; no pixel test pins the gradient direction.
<!-- SECTION:NOTES:END -->
