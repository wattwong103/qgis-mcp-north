---
id: TASK-15
title: Provenance — state ledger and eval replay
status: In Progress
assignee: []
created_date: '2026-10-08 12:00'
labels:
  - feature
  - provenance
dependencies:
  - TASK-12
  - TASK-13
priority: medium
ordinal: 15000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Third of four PRs (spec v2 §6 and the eval/depends_on parts of §7). Records load/style/project/eval calls (full and compound names) with their seq, snapshots the needed ones into depends_on for state-reading figures (render_map, and project exports/batches whose path equals the loaded project), registers qgis_eval through the hook, and makes export_session replay them: evals only with --allow-eval at run time, foreign evals only with trust_foreign=True.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Successful qgis_eval recorded through call_tool; a failed eval (exception set) is not
- [x] #2 Style A→B→A replays A last; export after project_load + style is state-reading; export of another project path clears the ledger; compound-mode ledger works
- [x] #3 depends_on entries carry inputs that reach check_inputs; unrecorded_state notes per spec §6
- [x] #4 Replay without --allow-eval prints eval hashes/first lines and exits 3; foreign evals dropped unless trust_foreign=True; include_evals=False drops all
- [x] #5 pytest + ruff green; DESIGN + CHANGELOG
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Plan: docs/superpowers/plans/2026-10-10-state-ledger.md (Native execution).
- The session reset replaces the headless executor (a fresh QGIS process); no plugin command.
- Foreign or excluded evals are reported as warnings (spec §7 Trust), not as skipped figures.
- Dependency calls are re-emitted per state-reading figure, so each figure replays its own snapshot.
- The export half of replay.py moved to session_export.py; generated scripts still import replay.main.
<!-- SECTION:NOTES:END -->
