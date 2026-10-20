---
id: TASK-18
title: Fixes from the 2026-10-20 live trial
status: In Progress
assignee: []
created_date: '2026-10-20 04:00'
labels:
  - bug
  - provenance
  - palettes
  - duckdb
dependencies: []
priority: high
ordinal: 18000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A live trial of the v1.15 features against real QGIS (headless, Windows) found three bugs; North chose the fixes on 2026-10-20 (one PR, refuse-with-hint for palettes).

B1 (HIGH): the first sidecar of an MCP server session hung the whole server until the client's 1800 s tool timeout. `provenance._git` ran git without redirecting stdin; over stdio mcp keeps a read pending on the server's stdin, Windows serializes I/O on that inherited handle, so git blocked at startup, `subprocess.run`'s post-kill `communicate()` has no timeout on Windows (the `Git\cmd\git.exe` launcher's child kept the pipes), and mcp 1.26 runs sync tools on the event loop, so every other call queued behind it.

B3 (MEDIUM-HIGH): QGIS's default style has no qualitative ColorBrewer ramps. `qgis_style_categorized`'s default `Set2` (and Paired, Dark2, ...) silently became Spectral; trajectory modes were drawn in QGIS's random default-symbol colours, so a replay never matched.

B4 (MEDIUM): `qgis_render_from_duckdb` sent DuckDB values straight into the JSON dispatch; a DECIMAL (DuckDB's type for `139.70`), DATE, TIMESTAMP, UUID or INTERVAL column failed the whole render with an unhinted TypeError. Unit tests missed it because FakeExecutor never encoded params.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 git runs with stdin closed; an AST test requires `stdin=` on every subprocess call in src/; a Windows test with a thread blocked on stdin gets a git sha back
- [x] #2 Qualitative ColorBrewer names resolve (first n colours, in order) in categorized styling, diagram maps and trajectory modes; trajectory renders are byte-identical across runs
- [x] #3 More categories than a qualitative scheme has, and unknown palette names, are refused with one actionable Next: hint (ExecutorError no longer appends a second)
- [x] #4 DuckDB DECIMAL becomes float, dates/times ISO text, other non-JSON values text; FakeExecutor fails like the wire on unencodable params
- [x] #5 Version 1.15.1, CHANGELOG, DESIGN; targeted tests, full suite and ruff run
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Reviews (code, python, security; fresh context, read-only; 2026-10-20): no CRITICAL/HIGH. Fixed in the one round: style_categorized's "Set2" default would refuse a bare call on >8 values (default is now None: Set2 up to 8, else Spectral); `_wire_value` turned LIST/STRUCT/MAP into repr text and BLOBs into unbounded text (now JSON / `<BLOB n bytes>`); qualitative palettes in graduated and link-density renders repeated colours past their size (now refused, link density before the join); `_git`'s timeout did not hold on Windows (Popen, no post-kill communicate); the stdin guard missed aliases, `from subprocess import`, `stdin=None` and os.system/getoutput (now caught, with self-tests, plugin scanned too); `{k}` hint via replace, not format; palette names cut to 64 chars in errors; Windows test tidied; live tests share one QGIS.
Parked from review: total dispatch payload has no size check before the plugin's 10 MB cap (long VARCHAR too, pre-existing); resolve git with shutil.which (cwd search on Windows, pre-existing, root is not tool input); DECIMAL(38,0) ids above 2^53 lose precision as floats; a fixed mode→colour map for known PFLOW modes; server-side palette validation before link-density aggregation; trajectory test proves determinism, not that the colours are Set2.
Parked from the trial (not in this PR): GeoPackage header churn breaks replay input checks (TASK-19, North chose header-masked hashing); unstyled layers (load_layer → render_map, basemap_paths) still draw in QGIS's random default colour; replay stops at the first failing block; mcp 1.x runs sync tools on the event loop, so one slow call freezes the server; the failed plain `uv sync` removed DuckDB's `_duckdb-stubs/*.pyi` from the main .venv (runtime unaffected; reinstall the package with the MCP server stopped).
<!-- SECTION:NOTES:END -->
