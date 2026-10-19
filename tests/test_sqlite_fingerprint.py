"""SQLite inputs: a header-only change (QGIS switching a GeoPackage to WAL) is not a data change.

Found in the 2026-10-20 trial: batch_render opened a GeoPackage for update, QGIS
switched it to WAL journal mode, and only the 100-byte header changed (read/write
version 1 -> 2, change counter, library version). The byte sha256 changed, so
every replay that read the file exited 2.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from qgis_mcp_workflows import datasources, executors, provenance, replay
from qgis_mcp_workflows.session_export import source_inputs
from tests.conftest import requires_headless

ZONES_GPKG = Path(__file__).resolve().parents[1] / "assets" / "zones_tokyo23.gpkg"


@pytest.fixture(autouse=True)
def _fresh_cache():
    provenance.reset_for_tests()
    yield
    provenance.reset_for_tests()


def _db(path: Path) -> Path:
    with closing(sqlite3.connect(path)) as con:
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("CREATE TABLE t (k TEXT, v INTEGER)")
        con.executemany("INSERT INTO t VALUES (?, ?)", [("a", 1), ("b", 2)])
        con.commit()
    return path


def _to_wal(path: Path) -> None:
    with closing(sqlite3.connect(path)) as con:
        con.execute("PRAGMA journal_mode=WAL")


def _edit(path: Path, sql: str) -> None:
    with closing(sqlite3.connect(path)) as con:
        con.execute(sql)
        con.commit()


def test_switching_to_wal_changes_the_bytes_but_not_the_sqlite_hash(tmp_path):
    db = _db(tmp_path / "z.gpkg")
    before = provenance.fingerprint(str(db))
    _to_wal(db)
    assert db.read_bytes()[18:20] == b"\x02\x02"  # the switch really happened
    provenance.reset_for_tests()  # NTFS mtime is coarse: never read the cached hash
    after = provenance.fingerprint(str(db))

    assert after["sha256"] != before["sha256"]
    assert after["sqlite_sha256"] == before["sqlite_sha256"] is not None


def test_a_data_change_changes_the_sqlite_hash(tmp_path):
    db = _db(tmp_path / "z.sqlite")
    before = provenance.fingerprint(str(db))["sqlite_sha256"]
    _edit(db, "UPDATE t SET v = 99 WHERE k = 'a'")
    provenance.reset_for_tests()
    assert provenance.fingerprint(str(db))["sqlite_sha256"] != before


@pytest.mark.parametrize("data", [b"zone_id,n\n1,2\n", b"SQLite format 3\x00 but too short"])
def test_other_files_get_no_sqlite_hash(tmp_path, data):
    f = tmp_path / "x.gpkg"  # the magic decides, never the extension
    f.write_bytes(data)
    assert "sqlite_sha256" not in provenance.fingerprint(str(f))


def test_the_hash_cap_covers_the_sqlite_hash(tmp_path, monkeypatch):
    db = _db(tmp_path / "z.db")
    monkeypatch.setattr(provenance, "hash_cap_bytes", lambda: 10)
    fp = provenance.fingerprint(str(db))
    assert fp["sha256"] is None and "sqlite_sha256" not in fp


def _recorded(path: Path) -> dict:
    fp = provenance.fingerprint(str(path))
    return {"path": str(path), "sha256": fp["sha256"], "bytes": fp["bytes"],
            **({"sqlite_sha256": fp["sqlite_sha256"]} if "sqlite_sha256" in fp else {})}


def test_replay_accepts_a_header_only_change_and_says_so(tmp_path):
    db = _db(tmp_path / "z.gpkg")
    entry = _recorded(db)
    _to_wal(db)
    provenance.reset_for_tests()
    notes: list[str] = []
    assert replay.check_inputs([entry], accepted=notes) == []
    assert len(notes) == 1 and "header-only change" in notes[0] and "z.gpkg" in notes[0]


def test_replay_still_refuses_a_data_change(tmp_path):
    db = _db(tmp_path / "z.gpkg")
    entry = _recorded(db)
    _edit(db, "DELETE FROM t WHERE k = 'b'")
    provenance.reset_for_tests()
    [line] = replay.check_inputs([entry], accepted=[])
    assert line.startswith("changed") and "sha256 differs" in line


def test_a_sidecar_without_the_field_keeps_the_byte_comparison(tmp_path):
    db = _db(tmp_path / "z.gpkg")
    entry = _recorded(db)
    del entry["sqlite_sha256"]  # recorded before 1.15.2
    _to_wal(db)
    provenance.reset_for_tests()
    [line] = replay.check_inputs([entry])
    assert "sha256 differs" in line


def test_a_header_change_with_writes_pending_in_the_wal_is_refused(tmp_path):
    # Review HIGH: switched to WAL and edited, the edit can sit in z.gpkg-wal while
    # the main file differs only in its header; that must not pass as unchanged.
    db = _db(tmp_path / "z.gpkg")
    entry = _recorded(db)
    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("INSERT INTO t VALUES ('c', 3)")
        con.commit()
        assert (tmp_path / "z.gpkg-wal").stat().st_size > 0
        provenance.reset_for_tests()
        notes: list[str] = []
        [line] = replay.check_inputs([entry], accepted=notes)
        assert line.startswith("changed") and "-wal" in line
        assert notes == []
    finally:
        con.close()


def test_a_recorded_wal_that_is_gone_is_a_note_not_a_refusal(tmp_path):
    # QGIS deletes the -wal when its last connection closes; whether the data
    # moved into the main file is the main file's check to say.
    gone = {"path": str(tmp_path / "z.gpkg-wal"), "sha256": "ab" * 32, "bytes": 32}
    notes: list[str] = []
    assert replay.check_inputs([gone], accepted=notes) == []
    assert len(notes) == 1 and "z.gpkg-wal" in notes[0]


def test_is_sqlite_never_opens_what_is_not_a_regular_file(tmp_path):
    assert provenance.is_sqlite(str(tmp_path)) is False  # a directory: stat first, no open


def test_the_script_inputs_carry_the_sqlite_hash():
    step = {"inputs": [{"path": "${DROPBOX_ROOT}/z.gpkg", "sha256": "aa", "bytes": 4, "sqlite_sha256": "bb"},
                       {"path": "${DROPBOX_ROOT}/v.csv", "sha256": "cc", "bytes": 2}]}
    found = {i["path"]: i for i in source_inputs([step])}
    assert found["${DROPBOX_ROOT}/z.gpkg"]["sqlite_sha256"] == "bb"
    assert "sqlite_sha256" not in found["${DROPBOX_ROOT}/v.csv"]


def test_a_pending_wal_file_is_an_implicit_input_with_a_note(tmp_path):
    db = _db(tmp_path / "z.gpkg")
    (tmp_path / "z.gpkg-wal").write_bytes(b"\x00" * 32)
    extra, notes, _ = datasources.discover([("zones_path", str(db))])
    assert [(a, Path(p).name) for a, p in extra] == [("zones_path:wal", "z.gpkg-wal")]
    assert any("-wal" in n and "--force" in n for n in notes)


@pytest.mark.parametrize("wal", [None, b""])
def test_no_wal_input_without_pending_writes(tmp_path, wal):
    db = _db(tmp_path / "z.gpkg")
    if wal is not None:
        (tmp_path / "z.gpkg-wal").write_bytes(wal)
    extra, notes, _ = datasources.discover([("zones_path", str(db))])
    assert extra == [] and notes == []


def test_a_wal_beside_a_file_that_is_not_sqlite_is_ignored(tmp_path):
    csv = tmp_path / "v.csv"
    csv.write_bytes(b"k,v\na,1\n")
    (tmp_path / "v.csv-wal").write_bytes(b"\x00" * 32)
    assert datasources.discover([("value_csv", str(csv))])[0] == []


def test_a_project_datasource_brings_its_pending_wal(tmp_path, monkeypatch):
    from tests.test_datasources import _qgs

    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    _db(tmp_path / "z.gpkg")
    (tmp_path / "z.gpkg-wal").write_bytes(b"\x00" * 32)
    qgs = tmp_path / "p.qgs"
    qgs.write_text(_qgs(("ogr", "./z.gpkg|layername=t")), encoding="utf-8")
    extra, notes, _ = datasources.discover([("template_qgz", str(qgs))])
    assert [(a, Path(p).name) for a, p in extra] == [
        ("template_qgz:datasource", "z.gpkg"), ("template_qgz:datasource:wal", "z.gpkg-wal")]
    assert any(n.startswith("template_qgz:datasource:") and "-wal" in n for n in notes)


def test_a_co_exported_sidecar_cannot_swap_in_another_sqlite_hash():
    honest = {"path": "${DROPBOX_ROOT}/z.gpkg", "sha256": "aa", "bytes": 4, "sqlite_sha256": "bb"}
    forged = {**honest, "sqlite_sha256": "ff"}
    found = source_inputs([{"inputs": [forged]}, {"inputs": [honest]}])
    assert sorted(i["sqlite_sha256"] for i in found) == ["bb", "ff"]  # both are checked


def test_a_header_with_unknown_format_bytes_is_not_header_only(tmp_path):
    db = _db(tmp_path / "z.gpkg")
    entry = _recorded(db)
    data = bytearray(db.read_bytes())
    data[18:20] = b"\x07\x07"  # masked, but no journal mode SQLite knows
    db.write_bytes(bytes(data))
    provenance.reset_for_tests()
    [line] = replay.check_inputs([entry], accepted=[])
    assert line.startswith("changed")


def test_main_prints_an_accepted_header_only_change(tmp_path, monkeypatch, capsys):
    from tests.test_replay import _run_main

    db = _db(tmp_path / "z.gpkg")
    entry = _recorded(db)
    _to_wal(db)
    provenance.reset_for_tests()
    code, _ = _run_main(tmp_path, monkeypatch, inputs=[entry], argv=["--out-dir", str(tmp_path / "o")])
    assert code == 0
    assert "header-only change" in capsys.readouterr().out


@requires_headless
async def test_a_figure_whose_geopackage_was_switched_to_wal_still_replays(tmp_path, monkeypatch):
    from qgis_mcp_workflows.executors.headless import HeadlessExecutor

    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    gpkg = tmp_path / "zones.gpkg"
    shutil.copyfile(ZONES_GPKG, gpkg)
    if gpkg.read_bytes()[18:20] != b"\x01\x01":
        pytest.skip("the fixture GeoPackage is not in rollback-journal mode")
    qgz = tmp_path / "template.qgz"
    server = importlib.import_module("qgis_mcp_workflows.server")
    executor = HeadlessExecutor()
    executors.set_executor(executor)
    try:
        build = ("from qgis.core import QgsProject, QgsVectorLayer\n"
                 "p = QgsProject.instance(); p.clear()\n"
                 f"p.addMapLayer(QgsVectorLayer({str(gpkg)!r}, 'wards', 'ogr'))\n"
                 f"ok = p.write({str(qgz)!r})\n")
        written = executor.dispatch("execute_code", {"code": build, "return_vars": ["ok"]}, timeout=120)
        assert written["return_values"]["ok"]
        await server.mcp.call_tool("qgis_batch_render", {"template_qgz": str(qgz), "attribute": "zone_id",
                                                         "values": ["13101"], "output_dir": str(tmp_path / "b")})
    finally:
        executor.shutdown()
        executors.set_executor(None)
    # Whether this batch render opens the GeoPackage for update depends on what
    # the QGIS session did before (in the trial it did); QGIS Desktop opening
    # the file for editing later does the same. Either way the header switches.
    if gpkg.read_bytes()[18:20] != b"\x02\x02":
        _to_wal(gpkg)
    assert gpkg.read_bytes()[18:20] == b"\x02\x02"
    [sidecar] = (tmp_path / "b").glob("*.provenance.json")
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    [source] = [i for i in record["implicit_inputs"] if i["argument"] == "template_qgz:datasource"]
    assert source["sqlite_sha256"]

    script = tmp_path / "replay.py"
    server.qgis_export_session(output_py=str(script), folder=str(tmp_path / "b"))
    done = subprocess.run([sys.executable, str(script), "--out-dir", str(tmp_path / "out")],
                          capture_output=True, text=True, timeout=300, env={**os.environ})
    assert done.returncode == 0, done.stdout + done.stderr
    assert "header-only change" in done.stdout
