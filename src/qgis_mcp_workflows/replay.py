"""Replay figures from their provenance sidecars (TASK-13).

Export half (``export_session``): read sidecars, follow ``made_by`` chains,
order the calls and write a standalone script. The script is built as an AST
from a fixed skeleton — a sidecar is unsigned input, so its values only ever
become literal constants.

Runtime half (``main`` and helpers): what the generated script imports. It
resolves ``${DROPBOX_ROOT}``, checks source inputs, remaps outputs and runs the
steps through a headless executor.

Spec: docs/superpowers/specs/2026-10-08-figure-provenance-design.md §7.
"""

from __future__ import annotations

import os

from qgis_mcp_workflows.provenance import ROOT_TOKEN, _relative_under, normalise


class ReplayError(Exception):
    """A replay precondition failed; ``main`` prints it and exits 2."""


def resolve(path: str) -> str:
    """Expand ``${DROPBOX_ROOT}`` (the only variable expanded) to this machine's root."""
    if not path.startswith(ROOT_TOKEN):
        return path
    root = os.environ.get("DROPBOX_ROOT", "").strip()
    if not root:
        raise ReplayError("set DROPBOX_ROOT to this machine's Dropbox folder, then re-run")
    full = normalise(os.path.join(root, path[len(ROOT_TOKEN):].lstrip("/")))
    if _relative_under(full, normalise(root)) is None:
        raise ReplayError(f"path leaves DROPBOX_ROOT: {path!r}")
    return full
