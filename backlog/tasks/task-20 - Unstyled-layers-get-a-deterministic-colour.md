---
id: TASK-20
title: Unstyled layers get a deterministic colour
status: In Progress
assignee: []
created_date: '2026-10-20 12:00'
labels:
  - bug
  - provenance
  - replay
dependencies: []
priority: medium
ordinal: 20000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Parked from the 2026-10-20 trial and reproduced live on 1.15.2: the same GeoPackage loaded twice with `qgis_load_layer` and rendered with identical `qgis_render_map` calls came out green and then purple. `add_vector_layer` keeps QGIS's default renderer, whose symbol colour is random (project style setting "random colors"), so figures of unstyled layers differ run to run and no replay of them can match. `basemap_paths` underlays are not affected (`_style_underlay_vector` styles them).

Fix (Claude's call; North skipped the design questions): an unstyled vector layer gets QGIS's default symbol with its colour taken from ColorBrewer Set2 by `zlib.crc32` of the file name, so one file always gets one colour, on every machine and in every replay. A layer that brings its own default style (a `.qml` beside it, a style stored in the GeoPackage) keeps it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The same file loaded twice, and in two separate QGIS processes, draws in the same colour
- [x] #2 The colour is Set2[crc32(file name) % 8]; two different file names map as computed
- [x] #3 A layer with its own default style (a .qml sidecar) keeps it
- [x] #4 Plugin stays Python 3.9 / PyQt6 compatible; CHANGELOG, DESIGN; full suite and ruff
<!-- AC:END -->
