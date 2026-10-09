---
id: TASK-16
title: Provenance — project and shapefile datasources
status: To Do
assignee: []
created_date: '2026-10-08 12:00'
labels:
  - feature
  - provenance
  - security
dependencies:
  - TASK-12
priority: low
ordinal: 16000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fourth of four PRs (spec v2 §9). Fingerprint .shp siblings and the local datasources a .qgz/.qgs references (delimited text, dbname=, relative paths), with hardened parsing: single member read with a 64 MB cap, DOCTYPE/ENTITY refused, UNC never opened, regular files under the project folder or DROPBOX_ROOT only, credentials redacted from remote datasources.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 .shp siblings fingerprinted
- [ ] #2 .qgz and .qgs datasources parsed (|layername, file:///?…, dbname=, ./ and ../); unparsed or out-of-root → unrecorded_state; databases/web → remote with credentials redacted
- [ ] #3 DOCTYPE/ENTITY documents refused; size cap enforced; UNC and file://host never opened (tests)
- [ ] #4 pytest + ruff green; CHANGELOG
<!-- AC:END -->
