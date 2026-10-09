---
id: TASK-14
title: Batch render target layer and non-blocking project reads
status: In Progress
assignee: []
created_date: '2026-10-08 11:00'
labels:
  - bug
  - upstream-review
dependencies: []
priority: high
ordinal: 14000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Second half of tranche A from the 2026-10-06 upstream comparison.

qgis_batch_render: the "saved active layer" lookup (`readPath("ActiveLayerID")`) never finds a layer — QGIS does not save the active layer in a project (checked in a Desktop-saved 3.40.5 .qgz) — so it fell back to registry order (layer ids sorted by name) and could filter a hidden or unrelated layer; without a layout it drew every registry layer, hidden ones included; it ignored setSubsetString's return (a refused filter saved the unfiltered map under the value's name); and its finally cleared the template's own filter for the rest of the session.

Project reads (project_load, export_layout, export_atlas, batch_render) block on QGIS Desktop's modal "Handle Unavailable Layers" dialog when a data source is missing (Dropbox roots differ per machine), until the client times out (upstream 44ae681).

North's decision (2026-10-08): optional `layer=` (name or id); default the top-most visible vector layer; response names the filtered layer.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 batch_render filters `layer=` or the top-most visible valid vector layer and returns `target_layer`; unknown/ambiguous/unavailable `layer=` errors list the vector layers
- [x] #2 Without a layout, only visible layers are drawn, in layer-tree (or custom) order (live test reads the rendered pixel)
- [x] #3 A refused filter is an error for that value and writes no file; field quoted with quotedColumnRef, value as an SQL literal; values name files inside output_dir only
- [x] #7 Without layer=, an unavailable top-most visible vector layer stops the batch (LAYER_UNAVAILABLE) instead of filtering the next one (added from review)
- [x] #4 The template's own subset string is restored after the batch
- [x] #5 Project reads go through one helper that dismisses the unavailable-layers dialog and reports `unavailable_layers` (project_load, export_layout, export_atlas, batch_render); a failed read carries QGIS's error text
- [x] #6 Live headless tests red on the old plugin, green after; pytest + ruff green; DESIGN, CLAUDE.md tool row, CHANGELOG updated
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Files: qgis_mcp_workflows_plugin/plugin.py (_open_project, _unavailable_layers, _dismiss_unavailable_layers_dialog, batch_render, _batch_target, _visible_layers), src/qgis_mcp_workflows/server.py (layer param, result fields), compound.py (layer passthrough), tests/test_plugin_live_batch.py, tests/test_batch_render.py, tests/test_project_load.py, tests/test_export_layout.py, tests/test_export_atlas.py, docs/DESIGN.md, CLAUDE.md, CHANGELOG.md.
Validation: uv run --no-sync pytest tests/ -q; uv tool run ruff check src/ tests/; live headless on QGIS LTR 3.40.
Dismissal is tested headlessly with a Python QDialog subclass named QgsHandleBadLayers; the real Desktop dialog (and its QGIS 4 class name) still needs a manual plugin-transport check.
<!-- SECTION:PLAN:END -->
