---
id: TASK-12
title: Figure provenance — sidecar core
status: In Progress
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
First of four PRs for figure provenance (`docs/superpowers/specs/2026-10-08-figure-provenance-design.md` v2, §3–§5, §8, §10–§11). Every file a registered tool writes gets `<file>.provenance.json`: bound call, inputs fingerprinted before the call (sha256 ≤ 256 MB), output fingerprint, `made_by` links verified by fingerprint, result summary, portable `${DROPBOX_ROOT}` paths, environment. `install.py` passes DROPBOX_ROOT into MCP client configs. Large task: waits for North to approve the spec.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A tool called through MCP (full and compound mode) writes one sidecar per written file matching spec §4; direct Python calls, failed calls, exempt tools and QGIS_MCP_WORKFLOWS_PROVENANCE=0 write none
- [x] #2 Inputs fingerprinted before the call (in-place input==output flagged; changed_during_call); outputs fingerprinted after; written files extracted per result model; both drift guards pass
- [x] #3 Portable paths per spec §8 (component match, realpath, no ~/Dropbox fallback); install.py writes DROPBOX_ROOT into client config env
- [x] #4 Atomic write (mkstemp + fsync + replace with retry); a failed write deletes the stale sidecar; tool results and schemas unchanged
- [x] #5 Environment never spawns headless for pure-Python tools and never caches a failure
- [ ] #6 pytest + ruff green; DESIGN §5, .gitignore, CHANGELOG updated
<!-- AC:END -->
