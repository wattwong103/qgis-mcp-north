---
id: TASK-22
title: Panel grid figures with linked scales (qgis_compose_panels)
status: In Progress
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
- [ ] #1 `qgis_compose_panels` draws rows × cols panels of one polygon layer from CSV columns, sharing one extent
- [ ] #2 Scales shared by row, column or all, capped at a percentile, reported in the result; one colour bar per scale group with formatted ticks (≥ on a capped top)
- [ ] #3 Font family (missing ones reported), text sizes, zone outline width and colour, page size in mm; PDF/SVG vector, PNG at dpi
- [ ] #4 Provenance sidecar and replay like every file-writing tool
- [ ] #5 Docs, counts (28 tools), version 1.16.0; full suite and ruff
<!-- AC:END -->
