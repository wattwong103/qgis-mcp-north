---
id: TASK-16
title: Provenance — project and shapefile datasources
status: In Progress
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
- [x] #1 .shp siblings fingerprinted
- [x] #2 .qgz and .qgs datasources parsed (|layername, file:///?…, dbname=, ./ and ../); unparsed or out-of-root → unrecorded_state; databases/web → remote with credentials redacted
- [x] #3 DOCTYPE/ENTITY documents refused; size cap enforced; UNC and file://host never opened (tests)
- [x] #4 pytest + ruff green; CHANGELOG
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Plan: docs/superpowers/plans/2026-10-10-datasources.md (executed inline).
- Ruling: every real QGIS project starts with <!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'> (found by the live test), so exactly that declaration is accepted; any other DOCTYPE and every ENTITY are refused as spec section 9 intends.
- Datasource notes of a project load used only as a state dependency are not carried into the figure's unrecorded_state (its files are fingerprinted in the dependency's inputs).
<!-- SECTION:NOTES:END -->
