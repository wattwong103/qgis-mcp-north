---
id: doc-2
title: handoff-2026-10-08-claude
type: other
created_date: '2026-10-08 10:30'
---


## Goal
Compare our fork with upstream nkarasiak/qgis-mcp (v0.15.0; we forked at v0.2.1 = b27df9a) and adopt what helps. North picked tranches A (tool bugs), B (mcp 2.x error hints), E (provenance + style_qml); 58 unused inherited plugin handlers stay.

## Done
- Upstream review: 5 read-only reviewers + my spot checks. Ranked tranches A–F recorded in memory `upstream-sync-review-2026-10`.
- PR #20 (TASK-10, branch task/task-10-tool-bugs, worktree .worktrees/task-10-tool-bugs): export_atlas tuple bug (PDF always failed; PNG exported twice), categorized classes_subset ignored, FakeExecutor now checks every dispatch against plugin.py's command table (AST) — it found od_csv dead param and weekly_figures demo sending pre-v1.2 link-density params.
- PR #21 (TASK-11, branch task/task-11-toolerror, worktree .worktrees/task-11-toolerror): typed errors subclass the SDK ToolError (mcp >= 2.1 masked every Next: hint); InvalidArgumentError / InputFileNotFoundError / dropped-socket mapping; suite green on mcp 1.26 and 2.3.
- PR #22 (TASK-14, branch task/task-14-batch-render, worktree .worktrees/task-14-batch-render): rest of tranche A — batch_render filters layer= or the top-most visible vector layer (QGIS never saved the active layer), draws only visible layers, refused filters are errors, template filter restored, value file names sanitised; project reads dismiss the unavailable-layers dialog and report unavailable_layers. 9 live tests red on old plugin, green after; 328 passed + pre-existing failure.
- PR #23 (design note, branch task/task-12-provenance-design): provenance spec v2 after feasibility/adversarial/security reviews (3 CRITICAL + 15 HIGH addressed). North decided: evals only with --allow-eval (+ trust_foreign), four PRs (TASK-12 core → TASK-13 export → TASK-15 ledger/evals → TASK-16 datasources), scripts not recorded, no ~/Dropbox fallback.
- Main clone .venv was wiped by a subagent's `uv run` (2026-10-06) and rebuilt with `uv sync --locked --all-extras` (uv.lock unchanged).

## Evidence
- TASK-10: 328 passed / 3 skipped / 1 failed (pre-existing Windows file lock in test_headless_executor.py::test_add_vector_layer_returns_layer_id, same on main); 8 live headless tests pass on QGIS 3.40.14; atlas tests red against main's plugin.
- TASK-11: 320 passed on locked 1.26 (+ same pre-existing failure); 318 passed on mcp 2.3.0 (ephemeral env); new tests red before the fix on both.
- Reviews: code-reviewer + python-reviewer on both PRs, 0 CRITICAL / 0 HIGH; MEDIUMs fixed. Codex not run (not usable on this account; a peer session relayed "codex review is not necessary this pass" from North on 2026-10-07 — unverified in this session).

## Update 2026-10-09 (conflict check)
- North merged #20 and #23. #21 and #22 then conflicted with main in CHANGELOG.md only (and with each other in DESIGN.md §5); no code conflicts.
- Tested resolutions: main+#21 337 passed; main+#22 345 passed; main+#21+#22 354 passed, ruff clean (each + the pre-existing Windows file-lock failure).
- Re-landed (git merge and force push are hook-blocked): #24 = #21 on main; #25 = #22 stacked on #24's commit, both targeting main. Merge #24 then #25. Replacement comments posted on #21/#22 — North to close them.

## Update 2026-10-09 (TASK-12 implemented)
- PR #26 (branch task/task-12-provenance-core, worktree .worktrees/task-12-provenance-core): provenance sidecar core per plan docs/superpowers/plans/2026-10-09-provenance-sidecar-core.md, executed natively with TDD; 390 passed (+ known failure); live headless smoke OK; code/python/security reviews (opus) → 7 findings fixed with red-first tests.
- Decision for North (security M1): keep *.provenance.json out of other repos/Overleaf — global core.excludesFile, other repos' .gitignore, or an exclude setting (follow-up task).

## Update 2026-10-09 (TASK-13 implemented)
- PR #27 (branch task/task-13-export-session, worktree .worktrees/task-13-export-session; stacked on #26, base main): qgis_export_session + src/qgis_mcp_workflows/replay.py per plan docs/superpowers/plans/2026-10-09-export-session.md (11 tasks, TDD, ledger complete and deleted).
- Reviews (opus code/python/security) all found one CRITICAL: a planted sidecar could emit qgis_eval into the replay script. Fix pass (red-first, 41 new tests): replayable tools = provenance._writes_files; arguments must agree with recorded inputs/outputs; hash-verified made_by/intermediates; per-fingerprint source checks; batch file-name, network-path, malformed/oversized gates; cross-OS out-dir mapping; DuckDB queries not replayed.
- Evidence: 469 passed / 3 skipped / 1 known Windows-lock failure; ruff clean; live headless record -> export -> fresh-process replay gives the same PNG size; FakeExecutor round trip reproduces the recorded (command, params).
- Parked from the reviews (in #27's body): DuckDB connection hardening (enable_external_access/autoload off), plugin batch containment after join, pydantic limits on replayed args, signature.bind, argparse mutual exclusion, real Dropbox conflicted-copy names, byte budget, sidecar-location check, compound/two-drive tests, spec §7 project reset (deviation).

## Update 2026-10-09 (#26/#27 conflicts after #24/#25 merged)
- North ran the main merges locally (git merge/force-push are hook-blocked, so rebasing was not an option); Claude resolved the conflicts. #26: 3 additive conflicts (CHANGELOG, DESIGN §5, test_compound_mode), merge d701560, 416 passed + known lock failure, CI green. #27: merged the updated #26 branch cleanly (0639254); bb77b2b reads tool annotations via tests/mcp_compat.field (red on mcp 2.3 before); 495 passed on the locked mcp, replay tests 78/78 on mcp 2.3.
- Main clone left untouched (uncommitted .gitignore and assets/zones_tokyo23.gpkg kept; behind origin/main by 5 — pull when those are settled).

## Update 2026-10-09 (merged)
- North merged #26 (13:17Z) and #27 (13:25Z); main = 691c42b. CI on main: #26's merge commit green; #27's merge commit was running at handoff time (its branch CI was green).

## Update 2026-10-10 (follow-ups)
- Main clone fast-forwarded to 691c42b; North's uncommitted .gitignore (/graft/ entry, re-added below the new *.provenance.json line) and assets/zones_tokyo23.gpkg (byte-identical to backup) kept. Backups in the session scratchpad.
- PR #28 (chore/task-status-sync): 11 tasks with merged PRs marked Done; TASK-2/4/6/7 still have unticked ACs for North to tick; TASK-3 stays In Progress by design (standing allowlist).
- PR #29 (task/task-17-duckdb-hardening): DuckDB queries limited to one SELECT over stored tables; external access off + locked; local spatial preloaded. Reviews: no CRITICAL/HIGH; parked PROJ +init oracle and resource limits (MEDIUM) + LOWs in the PR body. 504 passed + known lock failure.
- Removed 7 merged worktrees (task-10/11/12-core/12-design/13/14, conflict-check — its staged files were identical to #25). Local branch deletion is hook-blocked (human-owned): North to run `git branch -d` on the 14 merged branches.
- TASK-15 plan written and committed locally: .worktrees/task-15-state-ledger, docs/superpowers/plans/2026-10-10-state-ledger.md (10 tasks; ledger.py, session_export.py split, depends_on blocks with layer_N rebinding and session resets, --allow-eval exit 3). Awaiting North's approval + execution method.
- Side effect to disclose: probe scripts downloaded the DuckDB httpfs + spatial extensions (~84 MB) into C:\Users\north\.duckdb\extensions\v1.5.5 without asking. Noted in #29.

## Update 2026-10-10 (TASK-15 implemented)
- PR #30 (task/task-15-state-ledger, worktree .worktrees/task-15-state-ledger): ledger.py + depends_on for render_map / stateful project exports; session_export.py split from replay.py; replay blocks (reset_session, layer_N rebinding); --allow-eval gate (exit 3), --show-evals, failed replayed eval → exit 1; include_evals / trust_foreign / n_evals. Native execution of docs/superpowers/plans/2026-10-10-state-ledger.md, 10 tasks, ledger deleted after review.
- Final review (code/python/security, opus): 0 CRITICAL; fixed in one pass with red-first tests: Windows plugin re-read of the open project (plugin _open_project now compares normcase(abspath); live test), silent failed eval, misleading eval preview, Windows network spellings, trust_foreign from the sidecar's real location, stale ledger after failed export, reset after a rebuilt block, malformed depends_on/layer_ids, dependency replayed_inputs smuggling.
- Evidence: 562 passed + known lock failure; mcp 2.3 replay/ledger files 134 passed; live headless styled render_map replay same PNG size; CI green on #30.
- Parked (PR body): eval code copied into sidecars (privacy), shared-folder trust is documented not enforced (machine-local eval log idea), respawn detection, path-equality unification, eval-count cap, misc minors.

## Update 2026-10-10 (TASK-16 implemented; #30 needs main)
- North merged #28 and #29. #30 now conflicts with main in CHANGELOG.md only (both added a top section); CI on #30 green. git merge is hook-blocked for Claude: North runs `git -C H:/Dropbox/qgis-mcp-north/.worktrees/task-15-state-ledger merge origin/main`, Claude resolves (keep both sections), tests, pushes.
- TASK-16 (task/task-16-datasources, worktree .worktrees/task-16-datasources, stacked on task/task-15-state-ledger, NOT pushed): datasources.py — .shp siblings, .qgz/.qgs datasources (relative, |layername, file:///, dbname=, local /vsizip), hardened (single member, 64 MB read cap, only QGIS's fixed DOCTYPE, UNC/links never followed, roots incl. DROPBOX_ROOT realpath), redacted remote, notes; hook guarded so a failure never drops the record; project-load notes/remote reach later figures. Live test found every QGIS project carries a fixed DOCTYPE (spec §9 amended). Reviews (code retried after an API safeguard error, python, security) → one fix pass: OGR connection-string password leak, redact bypasses + quadratic regex, malformed-source record loss, POSIX backslash escape, link escape, caps/streaming. 609 passed + known lock failure; live tests pass.
- After #30 is updated: rebase task-16 (local, unpushed) onto it, push, open PR (base main, stacked on #30).
- Parked (TASK-16): per-call hashing byte budget, embedded-layer note, case-variant dedup, GDAL subdataset paths.

## Update 2026-10-10 (wrap-up)
- North ran the main merge on #30's branch; Claude resolved CHANGELOG (both sections), 571 passed + known lock failure, pushed 4ef085c. #30 is MERGEABLE/CLEAN, CI green.
- TASK-16 rebased (local, unpushed → no force) onto 4ef085c, re-verified (618 passed + known lock failure; mcp 2.3 68 passed; live tests pass), pushed → PR #31 (base main, stacked on #30).
- Removed the merged worktrees chore-task-status (#28) and task-17-duckdb-hardening (#29). Open worktrees: task-15-state-ledger (#30), task-16-datasources (#31).
- All four provenance PRs are done: #26 (core) and #27 (export) merged; #30 (ledger + evals) and #31 (datasources) open.

## Verify next
- North: merge #30, then #31 (it shrinks to its own 6 commits once #30 is in). Delete merged local branches (hook-blocked for Claude): chore/task-status-sync, task/task-17-duckdb-hardening, plus the 14 listed earlier.
- Parked follow-ups to file as tasks: eval-trust log (unforgeable), eval code in sidecars (privacy), per-call hashing budget, respawn detection, DuckDB PROJ +init oracle and resource limits, GDAL subdataset paths, embedded-layer notes.

## Open questions
- Whether DuckDB figures should become replayable behind --allow-eval now that the tool is hardened (#29).

## Owner next
North merged #30 (2026-10-10); #31 remains. Claude: next session picks a parked follow-up or the style_qml design (tranche E remainder).

Status: DONE_WITH_CONCERNS
- Codex cross-harness review not run on #20–#31 (not usable on this account).
- #30 and #31 exceed the ~300-line guideline (spec §12 split approved by North).
- #22 manual Desktop check: move a layer file of a test .qgz, qgis_project_load over the plugin transport → immediate response with unavailable_layers, no dialog left open.
- After merge: remove worktrees task-10-tool-bugs, task-11-toolerror, task-14-batch-render, task-12-provenance-design and the throwaway conflict-check (bulk discard is hook-blocked; it has a staged cherry-pick of no value).

## Open questions
- North to approve the provenance spec (PR #23); then writing-plans → TASK-12 implementation.
- Parked from the security review: qgis_render_from_duckdb should connect with enable_external_access=false.
- style_qml (second half of tranche E) not designed yet.
- Proposed (operator-owned): an unpinned-mcp CI leg (in #21's body).
- Parked: numeric-tolerant classes matching; empty atlas coverage error; HeadlessExecutor ignores QGIS_MCP_WORKFLOWS_REPO_ROOT (headless.py:158); plugin errors from message-less exceptions read "failed: ."; Windows file-lock test; install.py hardening (mklink via os.system, single .bak); SERVER_INSTRUCTIONS missing 12 tools.

## Owner next
North: close #21/#22; merge #24 then #25, then #26, then #27 (CHANGELOG rebase as needed); decide security M1; decide whether DuckDB figures should become replayable behind TASK-15's --allow-eval. Claude next: TASK-15 (ledger + evals behind --allow-eval) plan, or the parked DuckDB connection hardening. Decided 2026-10-08: batch_render keeps replace semantics for the template filter.

Status: DONE_WITH_CONCERNS
- Codex cross-harness review not run on #20–#27 (not usable on this account).
- #27 is over the ~300-line guideline (~680 source lines incl. the review fixes).
- Tranche E: provenance core and replay done (#26, #27); TASK-15/16 and style_qml not started.
