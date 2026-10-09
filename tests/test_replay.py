"""Replay runtime and export (TASK-13). No QGIS needed."""

from __future__ import annotations

import hashlib

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


# --- Task 2: check_inputs ---------------------------------------------------------


def _entry(path, data):
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def test_check_inputs_accepts_unchanged(tmp_path):
    f = tmp_path / "z.csv"
    f.write_bytes(b"abc")
    assert replay.check_inputs([_entry(f, b"abc")]) == []


def test_check_inputs_reports_changed_content(tmp_path):
    f = tmp_path / "z.csv"
    f.write_bytes(b"abd")
    [line] = replay.check_inputs([_entry(f, b"abc")])
    assert line.startswith("changed") and "sha256" in line


def test_check_inputs_compares_size_when_unhashed(tmp_path):
    f = tmp_path / "big.duckdb"
    f.write_bytes(b"0123")
    [line] = replay.check_inputs([{"path": str(f), "sha256": None, "bytes": 9}])
    assert "size 9 -> 4" in line


def test_check_inputs_reports_missing(tmp_path):
    [line] = replay.check_inputs([{"path": "H:/other-machine/x.csv", "sha256": None, "bytes": 3}])
    assert line.startswith("missing")


# --- Task 3: output_mapper ---------------------------------------------------------


def test_out_dir_maps_portable_paths(tmp_path):
    out = replay.output_mapper(["${DROPBOX_ROOT}/gufm/fig.png"], [], str(tmp_path / "out"), False, False)
    target = out("${DROPBOX_ROOT}/gufm/fig.png")
    assert target == str((tmp_path / "out" / "DROPBOX_ROOT" / "gufm" / "fig.png").resolve())
    assert (tmp_path / "out" / "DROPBOX_ROOT" / "gufm").is_dir()   # parent created


def test_out_dir_maps_absolute_paths_by_drive(tmp_path):
    out = replay.output_mapper(["C:/tmp/a.png"], [], str(tmp_path / "out"), False, False)
    assert out("C:/tmp/a.png") == str((tmp_path / "out" / "_abs" / "C" / "tmp" / "a.png").resolve())


def test_out_dir_refuses_escape(tmp_path):
    out = replay.output_mapper([], [], str(tmp_path / "out"), False, False)
    with pytest.raises(replay.ReplayError, match="leaves --out-dir"):
        out("${DROPBOX_ROOT}/../../../x.png")


def test_in_place_refuses_overwriting_an_input(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    with pytest.raises(replay.ReplayError, match="recorded inputs"):
        replay.output_mapper(["${DROPBOX_ROOT}/a.csv"], ["${DROPBOX_ROOT}/a.csv"], "", True, True)


def test_in_place_needs_yes_to_overwrite(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    (tmp_path / "fig.png").write_bytes(b"old")
    with pytest.raises(replay.ReplayError, match="--yes"):
        replay.output_mapper(["${DROPBOX_ROOT}/fig.png"], [], "", True, False)
    out = replay.output_mapper(["${DROPBOX_ROOT}/fig.png"], [], "", True, True)
    assert out("${DROPBOX_ROOT}/fig.png") == provenance.normalise(str(tmp_path / "fig.png"))
