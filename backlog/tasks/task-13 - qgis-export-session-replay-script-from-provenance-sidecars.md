---
id: TASK-13
title: qgis_export_session — replay script from provenance sidecars
status: To Do
assignee: []
created_date: '2026-10-08 10:00'
labels:
  - feature
  - provenance
  - upstream-review
dependencies:
  - TASK-12
priority: medium
ordinal: 13000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Second of four PRs (spec v2 §7, minus ledger/eval parts that land in TASK-15). `qgis_export_session(output_py, figures=…|folder=…)` builds a standalone replay script for atomic figures and made_by chains: AST-generated (no string templates), tool and argument names validated, ordered producer-first then (session_id, seq), stale sidecars detected, source-input check (exit 2 unless --force), outputs remapped under --out-dir with a containment check, guarded --in-place. State-reading figures are skipped ("needs session state") until TASK-15.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Script built via ast/ast.unparse; hostile sidecars (""", newlines, unknown tool, unknown argument key) are skipped or neutralised; skipped/warnings use fixed templates
- [ ] #2 (session_id, seq) identifies a call: sibling outputs share a step, identical calls stay two; producer-first ordering; stale sidecars skipped
- [ ] #3 Input check (sha256, else size; mtime a warning) exits 2 unless --force; unset DROPBOX_ROOT stops with a hint; ${NAME} expansion allow-listed with root containment
- [ ] #4 --out-dir remap (ROOTNAME / _abs/<drive>) with containment check; --in-place refuses outputs that are inputs, needs --yes, excludes --force
- [ ] #5 Round trip against FakeExecutor reproduces the original (command, params) sequence with remapped paths; live headless choropleth replay produces an equal-size PNG
- [ ] #6 Tool count bumped (CLAUDE.md, README, AGENTS.md, DESIGN, SERVER_INSTRUCTIONS); CHANGELOG
<!-- AC:END -->
