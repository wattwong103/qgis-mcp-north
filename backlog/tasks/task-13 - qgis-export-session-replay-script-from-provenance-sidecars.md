---
id: TASK-13
title: qgis_export_session — replay script from provenance sidecars
status: In Progress
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
- [x] #1 Script built via ast/ast.unparse; hostile sidecars (""", newlines, unknown tool, unknown argument key) are skipped or neutralised; skipped/warnings use fixed templates
- [x] #2 (session_id, seq) identifies a call: sibling outputs share a step, identical calls stay two; producer-first ordering; stale sidecars skipped
- [x] #3 Input check (sha256, else size; mtime a warning) exits 2 unless --force; unset DROPBOX_ROOT stops with a hint; ${NAME} expansion allow-listed with root containment
- [x] #4 --out-dir remap (ROOTNAME / _abs/<drive>) with containment check; --in-place refuses outputs that are inputs, needs --yes, excludes --force
- [x] #5 Round trip against FakeExecutor reproduces the original (command, params) sequence with remapped paths; live headless choropleth replay produces an equal-size PNG
- [x] #6 Tool count bumped (CLAUDE.md, README, AGENTS.md, DESIGN, SERVER_INSTRUCTIONS); CHANGELOG
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Plan: docs/superpowers/plans/2026-10-09-export-session.md (11 tasks). Deviations from the AC wording:
- #3: mtime is not compared at all (no warning row) - Dropbox sync rewrites mtimes, so the row would fire on every synced file.
- #5: the live replay test renders a trajectory (tests/fixtures/tiny_trajectory.csv), not a choropleth; same record -> export -> fresh-process replay path.
- #6: this repo has no AGENTS.md; counts bumped in CLAUDE.md, README, DESIGN, SERVER_INSTRUCTIONS.
- Spec §7 "project reset between sessions" is not emitted: state-reading calls are skipped until TASK-15, so no replayed step reads project state.

Review fix pass (code/python/security reviewers, 2026-10-09): replayable tools limited to those that record sidecars (a planted sidecar could emit qgis_eval); arguments must agree with recorded inputs/outputs; hash-verified made_by and intermediates; per-fingerprint source checks; batch file-name, network-path, malformed/oversized-sidecar gates; cross-OS out-dir mapping; DuckDB queries not replayed.
<!-- SECTION:NOTES:END -->
