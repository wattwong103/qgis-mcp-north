"""Replay runtime and export (TASK-13). No QGIS needed."""

from __future__ import annotations

import pytest

from qgis_mcp_workflows import provenance, replay


def test_resolve_expands_dropbox_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    assert replay.resolve("${DROPBOX_ROOT}/gufm/z.gpkg") == provenance.normalise(str(tmp_path / "gufm" / "z.gpkg"))


def test_resolve_leaves_other_paths_alone():
    assert replay.resolve("H:/elsewhere/x.csv") == "H:/elsewhere/x.csv"
    assert replay.resolve("${HOME}/x.csv") == "${HOME}/x.csv"   # allow-list: DROPBOX_ROOT only


def test_resolve_needs_the_root(monkeypatch):
    monkeypatch.delenv("DROPBOX_ROOT", raising=False)
    with pytest.raises(replay.ReplayError, match="set DROPBOX_ROOT"):
        replay.resolve("${DROPBOX_ROOT}/x.csv")


def test_resolve_refuses_to_leave_the_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path / "root"))
    with pytest.raises(replay.ReplayError, match="leaves DROPBOX_ROOT"):
        replay.resolve("${DROPBOX_ROOT}/../../etc/passwd")
