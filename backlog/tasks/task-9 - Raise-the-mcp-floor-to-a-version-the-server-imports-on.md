---
id: TASK-9
title: Raise the mcp floor to a version the server imports on
status: Done
assignee: []
created_date: '2026-10-02 10:00'
updated_date: '2026-10-09 23:10'
labels:
  - deps
dependencies: []
priority: medium
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-02 during TASK-7. pyproject declared mcp[cli]>=1.20.0, but the server cannot be imported on 1.20.0 or 1.21.0 (FastMCP InvalidSignature evaluating the tools' Annotated[..., Field(...)] forward refs). Suite run against each release: 1.21.1-1.30.0 pass (312 passed at 1.21.1). CI installs the locked 1.26.0 and cannot see this.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Floor raised to the first release the full suite passes on
- [x] #2 Guard test fails if the floor is lowered to a known-bad version
- [x] #3 uv.lock regenerated without moving any pinned package
<!-- AC:END -->

Merged in PR #19 (wattwong103/qgis-mcp-north).
