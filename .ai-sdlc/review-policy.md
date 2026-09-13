# Review policy and working rules — qgis-mcp-north

Injected into every Claude Code session by the ai-sdlc plugin. Codex reads it via AGENTS.md.
The plugin's generic governance skill mentions `pnpm build/test/lint`; **this file wins** for this repo.

## Branch and merge rules
- Every change starts from a backlog task (`backlog task create ...`) with acceptance criteria.
- Work on `ai-sdlc/<task-id>-<slug>` or `chore/<slug>`. Never commit or push to `main`; `.githooks/` enforces this.
- Open a PR and stop. The human merges. Never `gh pr merge`, never close PRs or issues, never force-push.
- `.ai-sdlc/**` is operator-owned. Propose changes in the PR description, do not edit.
- Never commit agent scaffolding: `.claude/`, `.codex/`, `.grok/`, `.agents/`, `openspec/`, `AGENTS.md`, `.mcp.json` edits with absolute paths.

## Pre-commit checklist (run locally, fix before committing)
```bash
uv run --no-sync pytest tests/ -q
uv tool run ruff check src/ tests/
```
- New tool or handler → a test under `tests/` using the FakeExecutor.
- `qgis_mcp_workflows_plugin/` must stay Python 3.9-compatible.
- Bump `pyproject.toml` and `qgis_mcp_workflows_plugin/metadata.txt` together on any version change.

## Review calibration
- Reviewers: report `{approved, findings[], summary}`. Minor style nits are not blocking.
- Cross-harness: if Claude implemented, Codex reviews (`codex review`), and vice versa.

## Session end
Write a handoff: `backlog doc create "handoff-YYYY-MM-DD-<harness>"` with goal, done, changed files, verify-next, open questions, who owns next.
