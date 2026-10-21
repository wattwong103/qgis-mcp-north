---
id: TASK-21
title: Render tools and decks create their output folder
status: Done
assignee: []
created_date: '2026-10-22 10:00'
labels:
  - bug
dependencies:
  - TASK-20
priority: low
ordinal: 21000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-20 while reproducing TASK-20: `qgis_render_map` into a folder that did not exist failed with "Failed to save render to ... Next: inspect inputs", which hid the cause. All render tools write through `_save_map_image`, which never created the folder, while spatial join, zonal stats, atlas and batch exports create theirs. `qgis_figures_to_pptx` had the same gap (a bare FileNotFoundError). The layout exporters (`qgis_compose_layout`, `qgis_export_layout`) already create folders; tests now pin that.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 `qgis_render_map` (and every tool through `_save_map_image`) writes into a folder that does not exist yet
- [x] #2 `qgis_figures_to_pptx` writes a deck into a new folder
- [x] #3 A save that still fails names what to check (writable folder, file not open, .png path)
- [x] #4 Live tests pin compose_layout and export_layout; full suite and ruff; version 1.15.4
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Review (code-reviewer, fresh context, read-only, 2026-10-22): APPROVE, no CRITICAL/HIGH/MEDIUM. Fixed: a folder that cannot be made (a file in the way, `?` in the name) now gets the actionable error too, with a test; a test comment moved back above its test. Parked: `render_map_base64(path=...)` (not an MCP tool) ignores `img.save`'s result; compose/export layout folder tests are characterisation tests (QGIS's exporter creates folders; unverified on QGIS 4). Also fixed in passing: DESIGN.md pointed at `PFLOW\docs\maps\`, moved to `PFLOW\20_taxi\docs\maps\` on 2026-10-20 (flagged by the PFLOW folder-cleanup session).
<!-- SECTION:NOTES:END -->
