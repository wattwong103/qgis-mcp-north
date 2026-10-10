---
id: TASK-6
title: transport=auto starts degraded when no QGIS backend is reachable
status: Done
assignee: []
created_date: '2026-10-01 19:14'
updated_date: '2026-10-09 23:10'
labels:
  - transport
dependencies: []
priority: medium
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Split out of TASK-4 (2026-10-02). With transport=auto, server._build_executor raises HeadlessUnavailableError at startup when port 9877 is closed and no headless launcher resolves, so the MCP connection closes ('Connection closed') instead of the client seeing an error. Start degraded instead: an UnavailableExecutor re-probes the plugin port per call, swaps in PluginExecutor once QGIS is up, else raises TransportUnavailableError (both reasons + Next:). Explicit --transport=headless still fails fast.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 transport=auto with no plugin and no headless starts the MCP server; tools return an actionable error
- [ ] #2 Opening QGIS Desktop later recovers without restarting the MCP server
- [ ] #3 Explicit --transport=headless still fails fast
- [ ] #4 Verified: MCP initialize + qgis_ping with QGIS_MCP_WORKFLOWS_QGIS_LAUNCHER=/nonexistent
<!-- AC:END -->

Merged in PR #15 (wattwong103/qgis-mcp-north).
