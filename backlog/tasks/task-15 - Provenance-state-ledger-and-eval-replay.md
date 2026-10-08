---
id: TASK-15
title: Provenance — state ledger and eval replay
status: To Do
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
- [ ] #1 Successful qgis_eval recorded through call_tool; a failed eval (exception set) is not
- [ ] #2 Style A→B→A replays A last; export after project_load + style is state-reading; export of another project path clears the ledger; compound-mode ledger works
- [ ] #3 depends_on entries carry inputs that reach check_inputs; unrecorded_state notes per spec §6
- [ ] #4 Replay without --allow-eval prints eval hashes/first lines and exits 3; foreign evals dropped unless trust_foreign=True; include_evals=False drops all
- [ ] #5 pytest + ruff green; DESIGN + CHANGELOG
<!-- AC:END -->
