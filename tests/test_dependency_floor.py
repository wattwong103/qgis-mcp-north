"""The declared mcp floor must be a version the server actually runs on.

pyproject said ``mcp[cli]>=1.20.0`` while the server could not even be
imported on 1.20.0 or 1.21.0 (FastMCP raised ``InvalidSignature`` evaluating
the tools' ``Annotated[..., Field(...)]`` forward references). Checked
2026-10-02 by running the suite against each release: 1.21.1 through 1.30.0
pass (312 passed at 1.21.1), 1.20.0 and 1.21.0 fail at import.

CI installs the locked version, so it cannot catch a too-low floor by itself;
this test keeps the floor from being lowered again without new evidence.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAST_KNOWN_BAD = (1, 21, 0)


def _mcp_floor() -> tuple[int, ...]:
    deps = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    spec = next(d for d in deps if re.match(r"mcp\s*(\[|[<>=!~]|$)", d))
    m = re.search(r">=\s*(\d+(?:\.\d+)*)", spec)
    assert m, f"mcp dependency has no lower bound: {spec!r}"
    return tuple(int(p) for p in m.group(1).split("."))


def test_mcp_floor_is_above_versions_the_server_cannot_import_on():
    assert _mcp_floor() > LAST_KNOWN_BAD, (
        f"mcp floor {_mcp_floor()} allows {LAST_KNOWN_BAD} or older, which cannot "
        "import the server (InvalidSignature); the first working release is 1.21.1"
    )
