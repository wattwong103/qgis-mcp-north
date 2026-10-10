---
id: TASK-7
title: Render tools error after a successful render
status: Done
assignee: []
created_date: '2026-10-02 09:00'
updated_date: '2026-10-09 23:10'
labels:
  - mcp
  - render
dependencies: []
priority: high
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-02 during the QGIS 4 Desktop check (TASK-4). Calling qgis_render_choropleth through MCP (mcp 1.26.0) with a real output PNG under 1.5 MB returns isError "1 validation error for ChoroplethResult … input_type=list", although the PNG is written correctly. Reproduced on main-based code. Cause: helpers.with_png_preview/maybe_preview returns a bare [TextContent, ImageContent] list while functools.wraps keeps the tool's model return annotation, so FastMCP validates the list against the output model. Fix: return CallToolResult(content=[text, image], structuredContent=model dump), which FastMCP passes through after validating structuredContent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A registered render tool with a real small PNG returns no error through FastMCP's call_tool, with both the image preview and the structured result
- [ ] #2 Python callers (tests, scripts) still get the pydantic model; missing/oversize PNGs unchanged
- [ ] #3 Regression test drives the real server registration (no QGIS needed)
- [ ] #4 Verified end to end: qgis_render_choropleth over stdio MCP on assets/zones_tokyo23.gpkg returns success with an image block
<!-- AC:END -->

Merged in PR #18 (wattwong103/qgis-mcp-north).
