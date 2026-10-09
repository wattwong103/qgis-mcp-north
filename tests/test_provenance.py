"""Unit tests for figure provenance (TASK-12). No QGIS needed."""

from __future__ import annotations

import os

import pytest

from qgis_mcp_workflows import executors, provenance


def test_executor_requests_counts_get_executor_calls(fake_executor):
    before = executors.executor_requests()
    executors.get_executor()
    executors.get_executor()
    assert executors.executor_requests() == before + 2


# --- Task 2: portable paths ---------------------------------------------------


def test_normalise_uses_forward_slashes_and_absolute(monkeypatch, tmp_path):
    # chdir first: os.path.relpath raises across Windows drives (C: temp vs H: repo).
    monkeypatch.chdir(tmp_path)
    expected = os.path.realpath(str(tmp_path / "a" / "b.csv")).replace("\\", "/")
    assert provenance.normalise(os.path.join("a", "b.csv")).casefold() == expected.casefold()


def test_portable_under_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    stored, reason = provenance.portable(str(tmp_path / "gufm" / "fig.png"))
    assert stored == "${DROPBOX_ROOT}/gufm/fig.png"
    assert reason is None


def test_portable_matches_whole_components_only(monkeypatch, tmp_path):
    root = tmp_path / "Dropbox"
    monkeypatch.setenv("DROPBOX_ROOT", str(root))
    stored, reason = provenance.portable(str(tmp_path / "Dropbox-old" / "x.png"))
    assert stored == provenance.normalise(str(tmp_path / "Dropbox-old" / "x.png"))
    assert reason == "outside DROPBOX_ROOT"


def test_portable_unset_root_is_machine_specific(monkeypatch, tmp_path):
    monkeypatch.delenv("DROPBOX_ROOT", raising=False)
    stored, reason = provenance.portable(str(tmp_path / "x.png"))
    assert stored == provenance.normalise(str(tmp_path / "x.png"))
    assert reason == "DROPBOX_ROOT unset"


@pytest.mark.parametrize("insensitive, expected", [(True, "${DROPBOX_ROOT}/x.png"), (False, None)])
def test_portable_case_rule(monkeypatch, tmp_path, insensitive, expected):
    monkeypatch.setattr(provenance, "_case_insensitive", lambda: insensitive)
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path / "Root"))
    stored, _ = provenance.portable(str(tmp_path / "ROOT" / "x.png"))
    if expected is None:
        assert not stored.startswith("${DROPBOX_ROOT}")
    else:
        assert stored == expected
