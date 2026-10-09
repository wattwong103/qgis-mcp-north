---
id: TASK-11
title: Tool errors reach the client on mcp 2.x
status: Done
assignee: []
created_date: '2026-10-06 23:30'
updated_date: '2026-10-09 23:10'
labels:
  - bug
  - deps
  - upstream-review
dependencies: []
priority: high
ordinal: 11000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-06 by the upstream comparison (upstream 4e2d830, 060bc25). mcp >= 2.1 masks every tool exception that is not the SDK's `ToolError` as "Error executing tool <name>" (`UnexpectedToolError`). Our base `QgisMcpWorkflowsError(Exception)` and the 25 bare `ValueError` argument checks therefore lose their message and `Next:` hint on 2.x. Reproduced: on mcp 2.3.0 `qgis_style_categorized` with a missing layer returns only "Error executing tool qgis_style_categorized"; on the locked 1.26.0 the full hint arrives. gufm runs the locked 1.26 (`--frozen`) and is unaffected; `install.py --remote` resolves mcp fresh and gets 2.3. Four tests also read camelCase result attributes that 2.x renamed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 `QgisMcpWorkflowsError` subclasses the SDK `ToolError` on mcp 1.x and 2.x (one import shim)
- [x] #2 Argument checks in compound.py, server.py tools and route.py raise `InvalidArgumentError` (a `QgisMcpWorkflowsError` and a `ValueError`) whose message ends with `Next:`
- [x] #3 A registered tool's failure carries its `Next:` hint through `mcp.call_tool` on mcp 1.26 and 2.3
- [x] #4 Full unit suite passes on the locked mcp 1.26 and on mcp 2.3 (tests read result fields through `tests/mcp_compat.py`)
- [x] #5 ruff clean; DESIGN §5 + CHANGELOG updated; an unpinned-mcp CI leg proposed in the PR body (operator-owned)
- [x] #6 Missing input files raise `InputFileNotFoundError` (also `FileNotFoundError`) and a dropped plugin socket raises `PluginUnavailableError`, both with `Next:` (added from review)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Files: src/qgis_mcp_workflows/errors.py, compound.py, server.py, route.py; tests/test_tool_errors.py, tests/mcp_compat.py, tests/test_preview.py, tests/test_compound_mode.py; docs/DESIGN.md; CHANGELOG.md.
Validation: uv run --no-sync pytest tests/ -q (locked 1.26); uv run --no-project --with-editable '.[dev,pptx,duckdb,network]' --with "mcp[cli]==2.3.0" --with pytest-asyncio pytest tests/ -q; uv tool run ruff check src/ tests/.
<!-- SECTION:PLAN:END -->

Merged in PR #24 (wattwong103/qgis-mcp-north).
