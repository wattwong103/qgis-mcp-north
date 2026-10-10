---
id: TASK-5
title: 'install.py: link the plugin into QGIS4 profiles too'
status: Done
assignee: []
created_date: '2026-10-01 19:13'
updated_date: '2026-10-09 23:10'
labels:
  - install
  - qgis4
dependencies: []
priority: medium
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
install.py qgis_plugins_dir() hard-codes .../QGIS/QGIS3 on linux/darwin/win32, so QGIS 4 Desktop (which reads .../QGIS/QGIS4/profiles/<p>) never sees the plugin. Found during TASK-4 (2026-10-02); the plugin itself was verified under QGIS 4.2.2 / PyQt 6.11 (initGui, unload, TCP ping offscreen). Install into every existing QGIS3/QGIS4 base dir (or add --qgis-major), keep tests/test_install.py green, drop the manual-symlink note from README.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 install.py links into QGIS4 profiles when that base dir exists
- [x] #2 tests cover both majors
- [x] #3 README manual-symlink note removed
<!-- AC:END -->

Merged in PR #14 (wattwong103/qgis-mcp-north).
