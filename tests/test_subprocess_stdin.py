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

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "qgis_mcp_workflows"
# The plugin too: in headless mode its stdin is the server-to-runner protocol pipe.
SCANNED = (SRC, REPO / "qgis_mcp_workflows_plugin")
SPAWNERS = {"run", "Popen", "call", "check_call", "check_output"}
SAFE_STDIN = {"DEVNULL", "PIPE"}
# Spawners that cannot be told what stdin to use.
FORBIDDEN = {("subprocess", "getoutput"), ("subprocess", "getstatusoutput"), ("os", "system"),
             ("os", "popen"), ("asyncio", "create_subprocess_exec"), ("asyncio", "create_subprocess_shell")}


def _problems(tree: ast.AST, where: str) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            found.append(f"{where}:{node.lineno} from subprocess import (calls would escape this scan)")
        if isinstance(node, ast.Import):
            found += [f"{where}:{node.lineno} import subprocess as {a.asname}"
                      for a in node.names if a.name == "subprocess" and a.asname]
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)):
            continue
        module, attr = node.func.value.id, node.func.attr
        if (module, attr) in FORBIDDEN or (module == "os" and attr.startswith("spawn")):
            found.append(f"{where}:{node.lineno} {module}.{attr} cannot close stdin")
        elif module == "subprocess" and attr in SPAWNERS:
            stdin = next((k.value for k in node.keywords if k.arg == "stdin"), None)
            if not (isinstance(stdin, ast.Attribute) and isinstance(stdin.value, ast.Name)
                    and stdin.value.id == "subprocess" and stdin.attr in SAFE_STDIN):
                found.append(f"{where}:{node.lineno} subprocess.{attr} without stdin=DEVNULL or PIPE")
    return found


def _sources():
    for root in SCANNED:
        for path in sorted(root.rglob("*.py")):
            yield path.relative_to(REPO), ast.parse(path.read_text(encoding="utf-8"))


def test_every_subprocess_the_server_or_plugin_spawns_closes_or_pipes_stdin():
    problems = [p for path, tree in _sources() for p in _problems(tree, str(path))]
    assert problems == [], f"children that would inherit the MCP or protocol stdin: {problems}"


def test_the_scan_sees_the_server_s_subprocess_calls():
    calls = [n for _, tree in _sources() for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"
             and n.func.attr in SPAWNERS]
    assert len(calls) >= 2  # provenance's git, the headless runner


@pytest.mark.parametrize("code", [
    "import subprocess\nsubprocess.run(['git'])",
    "import subprocess\nsubprocess.run(['git'], stdin=None)",
    "import subprocess, sys\nsubprocess.Popen(['git'], stdin=sys.stdin)",
    "from subprocess import run\nrun(['git'])",
    "import subprocess as sp\nsp.run(['git'], stdin=sp.DEVNULL)",
    "import subprocess\nsubprocess.getoutput('git status')",
    "import os\nos.system('git status')",
])
def test_the_guard_catches_each_way_to_inherit_stdin(code):
    assert _problems(ast.parse(code), "snippet")


def test_the_guard_accepts_a_closed_or_piped_stdin():
    code = ("import subprocess\nsubprocess.run(['git'], stdin=subprocess.DEVNULL)\n"
            "subprocess.Popen(['x'], stdin=subprocess.PIPE)")
    assert _problems(ast.parse(code), "snippet") == []


class _Git:
    """subprocess.Popen stand-in: answers, or hangs past every timeout."""

    def __init__(self, hang: bool) -> None:
        self.hang, self.returncode, self.calls = hang, 0, []

    def communicate(self, timeout=None):
        self.calls.append(("communicate", timeout))
        if self.hang:
            raise subprocess.TimeoutExpired("git", timeout)
        return "abc1234\n", None

    def kill(self):
        self.calls.append(("kill", None))

    def wait(self, timeout=None):
        self.calls.append(("wait", timeout))


def test_git_runs_with_stdin_closed(monkeypatch, tmp_path):
    seen = {}

    def popen(cmd, **kwargs):
        seen.update(kwargs)
        return _Git(hang=False)

    monkeypatch.setattr(provenance.subprocess, "Popen", popen)
    assert provenance._git(tmp_path, "rev-parse", "--short", "HEAD") == "abc1234"
    assert seen.get("stdin") is subprocess.DEVNULL


def test_a_git_that_outlives_its_timeout_is_abandoned_not_awaited(monkeypatch, tmp_path):
    # subprocess.run would call communicate() again, unbounded, after the kill:
    # on Windows that waits for a git launcher's child that still holds the pipe.
    git = _Git(hang=True)
    monkeypatch.setattr(provenance.subprocess, "Popen", lambda cmd, **kwargs: git)
    assert provenance._git(tmp_path, "status") is None
    assert git.calls[0] == ("communicate", provenance._GIT_TIMEOUT_S)
    assert ("kill", None) in git.calls
    assert [c for c in git.calls if c[0] == "communicate"] == [git.calls[0]]
    assert all(timeout is not None for name, timeout in git.calls if name == "wait")


CHILD = r"""
import json, os, sys, threading, time
threading.Thread(target=lambda: sys.stdin.buffer.read(1), daemon=True).start()
time.sleep(0.5)
from qgis_mcp_workflows import provenance
print(json.dumps(provenance._git_state()), flush=True)
os._exit(0)  # never wait on the stdin lock the reader thread holds
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows serializes I/O on an inherited stdin handle")
@pytest.mark.skipif(shutil.which("git") is None or not (SRC.parents[1] / ".git").exists(),
                    reason="needs git and a git checkout")
def test_git_state_returns_while_another_thread_reads_stdin():
    # stdin stays an open pipe that never gets data, as the MCP client's does.
    paths = [str(SRC.parent)] + [p for p in [os.environ.get("PYTHONPATH")] if p]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}
    lines: list[bytes] = []
    with subprocess.Popen([sys.executable, "-c", CHILD], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) as child:
        reader = threading.Thread(target=lambda: lines.append(child.stdout.readline()), daemon=True)
        reader.start()
        reader.join(30)
        blocked = reader.is_alive()
        child.kill()
        err = "" if blocked else child.stderr.read().decode("utf-8", "replace")[-2000:]
    assert not blocked, "git blocked on the inherited stdin (the 2026-10-20 hang)"
    assert lines and lines[0], f"the child printed nothing; stderr: {err}"
    state = json.loads(lines[0])
    assert state["git"], f"git failed under a pending stdin read: {state}; stderr: {err}"
