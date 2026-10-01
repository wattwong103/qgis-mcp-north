---
id: TASK-4
title: 'macOS QGIS 4.x: headless launcher, bundle env, degraded auto mode'
status: To Do
assignee: []
created_date: '2026-10-01 18:57'
updated_date: '2026-10-01 19:15'
labels:
  - macos
  - headless
  - qgis4
dependencies: []
priority: high
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-02 by the GUFM Mac runtime closeout (gufm/23_results/audits/code/2026-10-02-mac-runtime-closeout/). On a Mac with only QGIS-final-4_2_2.app (QGIS 4.2.2, Qt 6.11, vcpkg build) the server exits 1 at startup: _MACOS_LAUNCHER_GLOBS match only Contents/MacOS/bin/python3 (QGIS 3 layout); QGIS 4 ships Contents/MacOS/python (wrapper that sets PYTHONHOME) and a raw python3.12 that cannot start alone. _bundle_env assumes Resources/{proj,gdal} and QGIS_PREFIX_PATH=Contents/MacOS, which on QGIS 4 breaks pkgDataPath. The runner forces app name QGIS3, so QGIS 4 reads the QGIS3 profile. transport=auto raises at startup when the plugin port is closed and headless is unavailable. Plan: resolve the QGIS 4 wrapper after the QGIS 3 patterns; derive Contents by walking up to the .app; QGIS 4 data under Resources/qgis/{proj,gdal}, prefix = .app root; runner app name QGIS4 on Qgis 4.x; auto mode starts degraded with a re-probing executor and a tool-level error. Tests: tests/test_macos_support.py, new transport-selection tests. Validation: pytest (all extras), ruff, MCP initialize + qgis_ping via the gufm/.mcp.json command, live headless tests un-skip on the Mac.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Headless resolution on macOS finds <QGIS*.app>/Contents/MacOS/python for QGIS 4 bundles; QGIS 3 bin/python3 still preferred; never the raw python3.12
- [ ] #2 _bundle_env gives correct PROJ_LIB/GDAL_DATA/QGIS_PREFIX_PATH for both bundle layouts; unit tests need no QGIS
- [ ] #3 Runner uses the QGIS4 profile under QGIS 4.x and QGIS3 under 3.x
- [ ] #4 Verified on the Mac: MCP initialize + qgis_ping (headless) via the gufm/.mcp.json command; live headless tests run
- [ ] #5 Docs (DESIGN.md, CLAUDE.md, CHANGELOG) updated; QGIS 4 plugin install documented
- [ ] #6 Plugin map furniture renders under QGIS 4 / PyQt6 (scoped enums); CI-safe static guard
<!-- AC:END -->
