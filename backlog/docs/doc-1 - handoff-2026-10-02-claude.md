---
id: doc-1
title: handoff-2026-10-02-claude
type: other
created_date: '2026-10-01 19:28'
---


## Goal
Make qgis-mcp-workflows start and work on a Mac with only QGIS 4.2.2 (finding from gufm/23_results/audits/code/2026-10-02-mac-runtime-closeout/).

## Done
- PR #12 (TASK-4, branch task/task-4-macos-qgis4-headless): QGIS 4 launcher (`Contents/MacOS/python` wrapper), QGIS 4 bundle env, QGIS4 profile name in the runner, Qt6 scoped enums in the plugin's map furniture.
- PR #13 (TASK-6, branch task/task-6-auto-degraded, worktree .worktrees/task-6-auto-degraded): transport=auto starts degraded instead of exiting.
- Plugin symlinked into ~/Library/Application Support/QGIS/QGIS4/profiles/default/python/plugins/ (outside the repo; not yet enabled in QGIS).
- Reviews: code-reviewer + python-reviewer (TASK-4), code-reviewer (TASK-6); MEDIUMs fixed. No Codex review (model unsupported / out of credits; North: Claude only).

## Evidence
- TASK-4: pytest 295 passed / 2 skipped (13 live headless tests now run on QGIS 4.2.2); ruff clean; gufm/.mcp.json command → initialize OK, qgis_ping pong over headless, QGIS4 profile, CRSs valid, 35 ramps.
- TASK-6: pytest 276 passed, 1 pre-existing failure (TASK-2); degraded MCP probe returns the combined tool error.

## Verify next
- Enable "QGIS MCP Workflows" in QGIS 4 Plugin Manager, start it, then qgis_ping → transport plugin.
- On a QGIS-LTR machine: pytest tests/test_headless_executor.py + a choropleth with title/legend (QGIS 3 paths unverified).
- After merge: switch ~/Dropbox/qgis-mcp-north back to main (gufm sessions run whatever is checked out there); remove the TASK-6 worktree.

## Open questions
- Version bump (1.14.1) at release? Not done; CHANGELOG under Unreleased.
- ~/.codex/config.toml default model gpt-6.1-sol is unsupported on this account (left unchanged).
- Uncommitted .gitignore / uv.lock / zones_tokyo23.gpkg changes in the main clone are not owned by this session.

## Owner next
North: review + merge #12, #13. Follow-ups: TASK-5 (install.py QGIS4), TASK-2 (CI AGENTS.md).

Status: DONE_WITH_CONCERNS

## Update (same session, later)
- PR #14 (TASK-5, branch task/task-5-install-qgis4, worktree .worktrees/task-5-install-qgis4): install.py links QGIS3 + QGIS4 (when QGIS/QGIS4 exists). Stacked on #12 (base = task-4 branch); no CI ran yet (workflow seems main-only), expect it after #12 merges and #14 retargets. Reviewer MEDIUM (off-by-one parents[2]) fixed with a non-tautological test. Tests 300 passed + TASK-2 failure.
- #12 body now notes CI pytest red only on TASK-2; #12 and #13 conflict in CHANGELOG.md / CLAUDE.md only (second to merge needs a rebase).
- Desktop plugin ping still pending: computer-use blocked (macOS Accessibility/Screen Recording not granted to the Claude app); the plugin is linked but not enabled in QGIS 4.
- Merge order: #12 → #14 (retargets to main) → #13 (rebase for CHANGELOG/CLAUDE.md).

## Update 2 (after North enabled the plugin in QGIS 4)
- Desktop plugin VERIFIED on QGIS 4.2.2: fresh gufm-command server picks transport=plugin; qgis_ping pong/plugin; qgis_eval in Desktop: QGIS4 profile, CRSs valid, 35 ramps; choropleth with title/legend/scale bar/north arrow rendered correctly via the plugin (Qt6 furniture fix confirmed in Desktop).
- Conflict #12 vs #13 resolved: #13's commit cherry-picked onto task-4 as branch task/task-6-auto-degraded-stacked → PR #15 (stacked on #12, CHANGELOG/CLAUDE.md resolved; 302 passed + TASK-2). #13 marked "Replaced by #15" — North to close it (agent may not close PRs). git merge is hook-blocked (human-owned), so no merge commit was made.
- Live degraded→plugin recovery verified on #15 (relay 19878→9877): error first, then transport=plugin without restart.
- #14 and #15 don't conflict (git apply --check of #14's diff on #15 is clean).
- NEW pre-existing bug (not from these PRs): every real render via MCP errors with a ChoroplethResult validation error although the PNG is written (with_png_preview returns a list but keeps the model return annotation). Reproduced on main-based code. Filed as a task chip.
- Merge order now: #12 → #14 and #15 (either order) → close #13.

## Update 3 (stacked-PR recovery + TASK-2 + TASK-7)
- Mistake (mine): #14 and #15 merged into the task-4 branch after #12 had landed (GitHub only retargets stacked PRs when the base branch is deleted). Re-landed as #16 (branch task/task-8-reland-14-15 = old task-4 tip, no new code). North merged #16 and #17; origin/main = 42aba96 contains be7ef19, 41d91bc, 175767d. #13 closed by North.
- TASK-2 (#17, merged): AGENTS.md count check skips when the file is absent (North's ruling); clean main checkout 297 passed / 2 skipped / 0 failed.
- TASK-7 (#18, open): render tools no longer error after a successful render. maybe_preview returns CallToolResult (structured payload wrapped as {"result": …} for compound Union tools). Reviewer HIGH (compound mode) fixed with a test. main + #18: 312 passed / 2 skipped / 0 failed. E2E over stdio against QGIS 4.2.2 plugin in full and compound mode: isError false, [text, image], structured present.
- Shared checkout now on main (42aba96), foreign uncommitted changes untouched; gufm command → 26 tools, qgis_ping pong/plugin.
- Leftover: remote branch task/task-7-render-preview-result (pushed before the rebase onto new main; no PR) is unused; delete it on GitHub if you like. Worktree .worktrees/task-7-on-main stays until #18 merges.
- Follow-up idea (not filed): pyproject says mcp>=1.20 but the server does not import on 1.20 (InvalidSignature); the floor should be raised.

## Update 4
- #18 (TASK-7) merged (main ee2e4d8).
- TASK-9 open as #19: mcp floor >=1.21.1 (1.20.0/1.21.0 can't import the server; 1.21.1 → 1.30.0 pass; full suite at 1.21.1: 312 passed). uv.lock picks up the stale self-version stamp 1.13.0→1.14.0, the likely source of the recurring "foreign" uv.lock diff in the shared clone. That local diff will block `git pull --ff-only` after #19 merges: discard it first (`git checkout -- uv.lock`; North's call). Proposed (operator-owned) CI job: lowest-direct resolution.
- Worktrees: only .worktrees/task-9-mcp-floor remains, until #19 merges.
