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
Replay half of the figure-provenance design (`docs/superpowers/specs/2026-10-08-figure-provenance-design.md`, §7). `qgis_export_session(output_py, figures=…|folder=…)` builds a standalone script that re-makes the chosen figures from source data: follows made_by chains, de-duplicates calls, rebinds layer ids, replays evals with a banner, checks input fingerprints (stop unless --force), writes to a separate --out-dir by default (--in-place opt-in), resolves ${DROPBOX_ROOT} on the running machine. Depends on TASK-12.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Generated script compiles; calls producer-first and de-duplicated; layer ids rebound; evals bannered
- [ ] #2 Outputs remapped under --out-dir (default replay_<date>/); --in-place keeps original paths
- [ ] #3 Source-input mismatch stops with exit 2 and a table; --force continues; produced intermediates are not checked; unset DROPBOX_ROOT stops with a hint
- [ ] #4 Round trip: the script run against FakeExecutor dispatches the original (command, params) sequence with only output paths remapped
- [ ] #5 Live headless: choropleth → export_session → replay produces the PNG
- [ ] #6 Compound qgis_export(kind="session"); DESIGN §4 + SERVER_INSTRUCTIONS + CHANGELOG updated
<!-- AC:END -->
