---
id: TASK-12
title: Figure provenance sidecars and dependency ledger
status: To Do
assignee: []
created_date: '2026-10-08 10:00'
labels:
  - feature
  - provenance
  - upstream-review
dependencies: []
priority: medium
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Record-side half of the figure-provenance design (`docs/superpowers/specs/2026-10-08-figure-provenance-design.md`, §3–§6, §8–§10). Every file a registered tool writes gets `<file>.provenance.json` with the bound call, fingerprinted inputs (sha256 under a 256 MB cap), `made_by` links, portable `${DROPBOX_ROOT}` paths, environment, and — for state-reading figures — the load/style/eval calls they depended on. Large task: waits for North to approve the spec.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A tool called through MCP writes one sidecar per written file matching spec §4; direct Python calls, failed calls and QGIS_MCP_WORKFLOWS_PROVENANCE=0 write none
- [ ] #2 Inputs classified per spec §5 (incl. .shp siblings, .qgz datasources, made_by); a drift-guard test fails on any unclassified path-like tool parameter
- [ ] #3 Ledger per spec §6: render_map after load_layer + styles + eval gets exactly load, last style, eval; project_load clears; unseen layer_ids land in unrecorded_state
- [ ] #4 Paths under DROPBOX_ROOT stored as ${DROPBOX_ROOT}/…; others listed in machine_specific
- [ ] #5 A sidecar write failure never fails the tool (logged, no .tmp left); response schemas unchanged
- [ ] #6 pytest + ruff green; DESIGN.md §5 + CHANGELOG updated
<!-- AC:END -->
