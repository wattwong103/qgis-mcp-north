"""Figure provenance: a sidecar next to every file an MCP tool call writes.

Spec: docs/superpowers/specs/2026-10-08-figure-provenance-design.md (v2).
Only the registered (MCP) copy of a tool is wrapped (``with_provenance`` in
server._maybe_tool / _maybe_compound_tool); Python callers get the bare
function and record nothing.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("qgis_mcp_workflows.provenance")

SCHEMA = "qgis-mcp-workflows/provenance@1"
SIDECAR_SUFFIX = ".provenance.json"
ROOT_TOKEN = "${DROPBOX_ROOT}"


def _case_insensitive() -> bool:
    return os.name == "nt"


def normalise(path: str) -> str:
    """Absolute, ``~`` expanded, forward slashes — the one form paths are compared in."""
    return os.path.abspath(os.path.expanduser(str(path))).replace("\\", "/")


def _relative_under(path: str, root: str) -> str | None:
    """``path`` relative to ``root`` if root is a whole-component prefix, else None."""
    parts = path.rstrip("/").split("/")
    root_parts = root.rstrip("/").split("/")
    if len(parts) < len(root_parts):
        return None
    head = parts[: len(root_parts)]
    if _case_insensitive():
        same = [p.casefold() for p in head] == [r.casefold() for r in root_parts]
    else:
        same = head == root_parts
    return "/".join(parts[len(root_parts):]) if same else None


def portable(path: str) -> tuple[str, str | None]:
    """(stored form, machine-specific reason or None).

    Under DROPBOX_ROOT (or its realpath — the macOS CloudStorage folder behind
    ~/Dropbox) a path is stored as ``${DROPBOX_ROOT}/<rest>`` so it resolves on
    every machine. There is no ~/Dropbox fallback: on Windows that is a
    different Dropbox.
    """
    norm = normalise(path)
    root = os.environ.get("DROPBOX_ROOT", "").strip()
    if not root:
        return norm, "DROPBOX_ROOT unset"
    roots = {normalise(root), normalise(os.path.realpath(root))}
    for candidate in (norm, normalise(os.path.realpath(norm))):
        for r in roots:
            rest = _relative_under(candidate, r)
            if rest is not None:
                return (f"{ROOT_TOKEN}/{rest}" if rest else ROOT_TOKEN), None
    return norm, "outside DROPBOX_ROOT"
