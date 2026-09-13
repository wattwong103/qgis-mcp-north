---
id: TASK-2
title: 'Fix CI: test_agents_md_workflow_count reads the gitignored AGENTS.md'
status: To Do
assignee: []
created_date: '2026-09-13 18:42'
labels:
  - ci
dependencies: []
priority: medium
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Pre-existing on main (red since 2026-09-12). tests/test_docs_consistency.py::test_agents_md_workflow_count reads AGENTS.md, which has been gitignored since d1dd995 (2026-08-31), so CI never sees it. Decide: track a shared AGENTS.md again, or point the test at CLAUDE.md, or drop the assertion.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 pytest (py3.12) job green on main
<!-- AC:END -->
