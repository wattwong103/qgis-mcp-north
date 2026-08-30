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
