---
id: TASK-17
title: DuckDB queries cannot touch files or the network
status: In Progress
assignee: []
created_date: '2026-10-09 23:20'
labels:
  - security
  - duckdb
dependencies: []
priority: medium
ordinal: 17000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Parked from the TASK-12 and TASK-13 security reviews. `qgis_render_from_duckdb` opens the database read-only, but read-only only protects that database: a query can still read any file (`read_text`, `read_csv`, `ST_Read`), write files (`COPY ... TO`, reachable by closing the LIMIT wrapper's parenthesis and adding statements), and download or load extensions (`INSTALL httpfs`, autoload), all with the user's rights. Verified on DuckDB 1.5.5 before the fix. Fix: load the locally installed spatial extension (so `ST_AsText(geom)` keeps working, as DESIGN §5 documents), then `SET enable_external_access = false` and `SET lock_configuration = true` before the query runs, with extension autoinstall/autoload off; and require the query to be exactly one SELECT (`duckdb.extract_statements`).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A query cannot read a file outside the database (read_text) — error, nothing dispatched
- [x] #2 The LIMIT-wrapper escape with COPY ... TO writes no file and errors
- [x] #3 More than one statement, or a non-SELECT, is refused with a Next: hint
- [x] #4 The lockdown cannot be undone from SQL (SET enable_external_access / lock_configuration refused)
- [x] #5 ST_AsText on a GEOMETRY column still works when spatial is installed locally (skipped where it is not)
- [x] #6 Existing DuckDB tests, full suite and ruff green; DESIGN/CLAUDE.md/CHANGELOG say what the connection allows
<!-- AC:END -->
