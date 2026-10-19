"""Subprocesses the MCP server spawns must not inherit its stdin.

Over stdio, mcp's transport keeps one thread blocked reading the server's stdin.
On Windows a child that inherits that handle blocks at startup until the read
completes, i.e. until the client next writes. Found live on 2026-10-20: the first
sidecar of a session ran ``git status`` from ``provenance._git_state`` and the
whole server hung for the client's 1800 s tool timeout.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from qgis_mcp_workflows import provenance

SRC = Path(__file__).resolve().parents[1] / "src" / "qgis_mcp_workflows"
SPAWNERS = {"run", "Popen", "call", "check_call", "check_output"}


def _subprocess_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in SPAWNERS and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"):
            yield node


def test_every_subprocess_the_server_spawns_sets_stdin():
    missing = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _subprocess_calls(tree):
            if not any(k.arg == "stdin" for k in call.keywords):
                missing.append(f"{path.relative_to(SRC.parent)}:{call.lineno}")
    assert missing == [], f"subprocess calls that inherit the MCP server's stdin: {missing}"


def test_the_scan_sees_the_server_s_subprocess_calls():
    found = sum(1 for p in SRC.rglob("*.py") for _ in _subprocess_calls(ast.parse(p.read_text(encoding="utf-8"))))
    assert found >= 2  # provenance's git, the headless runner


def test_git_runs_with_stdin_closed(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="abc1234\n", stderr="")

    monkeypatch.setattr(provenance.subprocess, "run", fake_run)
    assert provenance._git(tmp_path, "rev-parse", "--short", "HEAD") == "abc1234"
    assert seen.get("stdin") is subprocess.DEVNULL


CHILD = r"""
import json, sys, threading, time
threading.Thread(target=lambda: sys.stdin.buffer.read(1), daemon=True).start()
time.sleep(0.5)
from qgis_mcp_workflows import provenance
print(json.dumps(provenance._git_state()), flush=True)
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows serializes I/O on an inherited stdin handle")
@pytest.mark.skipif(shutil.which("git") is None or not (SRC.parents[1] / ".git").exists(),
                    reason="needs git and a git checkout")
def test_git_state_returns_while_another_thread_reads_stdin():
    # stdin stays an open pipe that never gets data, as the MCP client's does.
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(SRC.parent), os.environ.get("PYTHONPATH", "")])}
    child = subprocess.Popen([sys.executable, "-c", CHILD], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    lines: list[bytes] = []
    reader = threading.Thread(target=lambda: lines.append(child.stdout.readline()), daemon=True)
    reader.start()
    reader.join(30)
    try:
        assert not reader.is_alive(), "git blocked on the inherited stdin (the 2026-10-20 hang)"
        state = json.loads(lines[0])
        assert state["git"], f"git failed under a pending stdin read: {state}"
    finally:
        child.kill()
        child.wait(10)
