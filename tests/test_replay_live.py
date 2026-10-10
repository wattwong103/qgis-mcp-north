"""Record a real headless render, export it, replay it in a fresh process."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

from qgis_mcp_workflows import executors, ledger, provenance
from qgis_mcp_workflows.executors.headless import HeadlessExecutor
from tests.conftest import requires_headless

pytestmark = requires_headless

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "tiny_trajectory.csv")


async def test_trajectory_replays_to_the_same_size(tmp_path, monkeypatch):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    provenance.reset_for_tests()
    server = importlib.import_module("qgis_mcp_workflows.server")
    data = tmp_path / "traj.csv"
    data.write_bytes(Path(FIXTURE).read_bytes())
    executor = HeadlessExecutor()
    executors.set_executor(executor)
    try:
        await server.mcp.call_tool("qgis_render_trajectory",
                                   {"input_path": str(data), "output_png": str(tmp_path / "t.png")})
    finally:
        executor.shutdown()
        executors.set_executor(None)
    script = tmp_path / "replay.py"
    server.qgis_export_session(output_py=str(script), figures=[str(tmp_path / "t.png")])
    done = subprocess.run([sys.executable, str(script), "--out-dir", str(tmp_path / "out")],
                          capture_output=True, text=True, timeout=300, env={**os.environ})
    assert done.returncode == 0, done.stdout + done.stderr
    replayed = tmp_path / "out" / "DROPBOX_ROOT" / "t.png"
    assert replayed.stat().st_size == (tmp_path / "t.png").stat().st_size


ZONES = os.path.join(os.path.dirname(__file__), "fixtures", "tiny_zones.geojson")


async def test_styled_map_replays_to_the_same_size(tmp_path, monkeypatch):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    provenance.reset_for_tests()
    server = importlib.import_module("qgis_mcp_workflows.server")
    zones = tmp_path / "zones.geojson"
    zones.write_bytes(Path(ZONES).read_bytes())
    executor = HeadlessExecutor()
    executors.set_executor(executor)
    try:
        await server.mcp.call_tool("qgis_load_layer", {"path": str(zones)})
        [layer_id] = list(ledger._layers)  # reset_for_tests() emptied the ledger: this is the one just loaded
        await server.mcp.call_tool("qgis_style_categorized", {"layer_id": layer_id, "field": "zone_id"})
        await server.mcp.call_tool("qgis_render_map", {"layer_ids": [layer_id], "output_png": str(tmp_path / "m.png")})
    finally:
        executor.shutdown()
        executors.set_executor(None)
    script = tmp_path / "replay.py"
    result = server.qgis_export_session(output_py=str(script), figures=[str(tmp_path / "m.png")])
    assert result.skipped == []
    done = subprocess.run([sys.executable, str(script), "--out-dir", str(tmp_path / "out")],
                          capture_output=True, text=True, timeout=300, env={**os.environ})
    assert done.returncode == 0, done.stdout + done.stderr
    replayed = tmp_path / "out" / "DROPBOX_ROOT" / "m.png"
    assert replayed.stat().st_size == (tmp_path / "m.png").stat().st_size


async def test_a_qgis_written_project_has_its_datasource_fingerprinted(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    provenance.reset_for_tests()
    server = importlib.import_module("qgis_mcp_workflows.server")
    zones = tmp_path / "zones.geojson"
    zones.write_bytes(Path(ZONES).read_bytes())
    qgz = tmp_path / "template.qgz"
    executor = HeadlessExecutor()
    executors.set_executor(executor)
    try:
        build = ("from qgis.core import QgsProject, QgsVectorLayer\n"
                 "p = QgsProject.instance(); p.clear()\n"
                 f"p.addMapLayer(QgsVectorLayer({str(zones)!r}, 'zones', 'ogr'))\n"
                 f"ok = p.write({str(qgz)!r})\n")
        written = executor.dispatch("execute_code", {"code": build, "return_vars": ["ok"]}, timeout=120)
        assert written["return_values"]["ok"]
        await server.mcp.call_tool("qgis_batch_render", {"template_qgz": str(qgz), "attribute": "zone_id",
                                                         "values": ["Z01"], "output_dir": str(tmp_path / "batch")})
    finally:
        executor.shutdown()
        executors.set_executor(None)
    [sidecar] = (tmp_path / "batch").glob("*.provenance.json")
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    sources = [i for i in record["implicit_inputs"] if i["argument"] == "template_qgz:datasource"]
    assert [s["path"] for s in sources] == ["${DROPBOX_ROOT}/zones.geojson"] and sources[0]["sha256"]
