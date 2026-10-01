"""Docs must agree with the code about how many tools there are.

The tool count had drifted five ways at once (12 / 13 / 15 / 17 across CLAUDE.md,
README.md and docs/DESIGN.md) because every file restated it by hand. CLAUDE.md
makes DESIGN.md the spec, so a wrong number there is a wrong spec. This test
pins the documented counts to what `server.py` actually registers.

Fixing a failure here means updating the doc, not loosening the test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

ESCAPE_HATCH = "qgis_eval"


def _registered_tools() -> list[str]:
    src = (REPO / "src" / "qgis_mcp_workflows" / "server.py").read_text(encoding="utf-8")
    return re.findall(r"^def (qgis_\w+)", src, re.M)


def test_escape_hatch_is_registered():
    assert ESCAPE_HATCH in _registered_tools()


def test_claude_md_workflow_count():
    total = len(_registered_tools())
    text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert f"- {total - 1} workflow tools + 1 escape hatch" in text
    assert f"## MCP Tools ({total} total" in text


def test_readme_tool_count():
    total = len(_registered_tools())
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert f"## Tools ({total} standalone" in text


def _check_agents_md(path: Path, total: int) -> None:
    """AGENTS.md has been gitignored since 2026-08-31, so CI never has it.

    Ruling (North, 2026-10-02): check it when it exists, skip with a clear
    reason when it is absent. Local drift is still caught, and CI stays green.
    """
    if not path.exists():
        pytest.skip(f"{path.name} is absent (gitignored, so not in CI checkouts)")
    text = path.read_text(encoding="utf-8")
    assert f"- {total - 1} workflow tools + 1 escape hatch" in text
    assert f"## MCP Tools ({total} total" in text


def test_agents_md_workflow_count():
    _check_agents_md(REPO / "AGENTS.md", len(_registered_tools()))


def test_agents_md_check_skips_when_absent(tmp_path):
    with pytest.raises(pytest.skip.Exception, match="absent"):
        _check_agents_md(tmp_path / "AGENTS.md", 26)


def test_agents_md_check_still_catches_drift(tmp_path):
    stale = tmp_path / "AGENTS.md"
    stale.write_text("- 24 workflow tools + 1 escape hatch\n## MCP Tools (25 total\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        _check_agents_md(stale, 26)
    current = tmp_path / "AGENTS.md"
    current.write_text("- 25 workflow tools + 1 escape hatch\n## MCP Tools (26 total\n", encoding="utf-8")
    _check_agents_md(current, 26)


def test_server_docstring_does_not_hardcode_stale_count():
    src = (REPO / "src" / "qgis_mcp_workflows" / "server.py").read_text(encoding="utf-8")
    assert "v0.2 scaffold" not in src
    assert "12 workflow tools" not in src


def test_design_md_tool_surface_heading():
    """DESIGN.md is the spec — its §4 heading is the authoritative count."""
    total = len(_registered_tools())
    text = (REPO / "docs" / "DESIGN.md").read_text(encoding="utf-8")
    assert f"## 4. Tool surface ({total - 1} workflow + 1 escape hatch)" in text
    assert f"**{total - 1} workflow tools + 1 escape hatch" in text


def test_versions_stay_in_sync():
    """CLAUDE.md: the two version files must be bumped together.

    The QGIS plugin repository rejects re-uploads at the same version, so a
    mismatch here means a release that cannot be published.
    """
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    metadata = (REPO / "qgis_mcp_workflows_plugin" / "metadata.txt").read_text(encoding="utf-8")
    pv = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    mv = re.search(r"^version=(.+)$", metadata, re.M)
    assert pv and mv, "version line missing from pyproject.toml or metadata.txt"
    assert pv.group(1) == mv.group(1).strip(), (
        f"pyproject.toml={pv.group(1)} but metadata.txt={mv.group(1).strip()}"
    )
