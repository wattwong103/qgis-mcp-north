"""Record a real headless render, export it, replay it in a fresh process."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

from qgis_mcp_workflows import executors, provenance
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
