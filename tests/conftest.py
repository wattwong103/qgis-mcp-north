"""Shared pytest fixtures."""

from __future__ import annotations

import functools
import socket
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from qgis_mcp_workflows.helpers import DEFAULT_HOST, DEFAULT_PORT


def _plugin_reachable(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@functools.cache
def _headless_available() -> bool:
    """Can PyQGIS actually be imported through the resolved launcher?

    Note this asks a different question from "did a launcher path resolve".
    ``HeadlessExecutor._resolve_launcher()`` falls back to ``sys.executable`` on
    Linux and macOS, which always exists — so a path-only check answers True on
    a machine with no QGIS at all, and the live tests run and fail instead of
    skipping. CI caught exactly that.

    The previous heuristic had the opposite flaw: it looked for ``qgis`` on PATH,
    which a macOS ``QGIS.app`` install does not put there, so headless tests
    skipped on a machine where headless works.

    Spawning the interpreter is the only honest answer. Cached — it costs one
    subprocess per session, at collection time.
    """
    from qgis_mcp_workflows.errors import HeadlessUnavailableError
    from qgis_mcp_workflows.executors.headless import HeadlessExecutor

    try:
        launcher = HeadlessExecutor._resolve_launcher()
    except HeadlessUnavailableError:
        return False
    try:
        probe = subprocess.run(
            [launcher, "-c", "import qgis.core"],
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


PLUGIN_AVAILABLE = _plugin_reachable()
HEADLESS_AVAILABLE = _headless_available()

requires_plugin = pytest.mark.skipif(
    not PLUGIN_AVAILABLE,
    reason=f"QGIS plugin not reachable at {DEFAULT_HOST}:{DEFAULT_PORT} — start QGIS with the plugin enabled",
)
requires_headless = pytest.mark.skipif(
    not HEADLESS_AVAILABLE,
    reason=(
        "no PyQGIS importable from the resolved launcher — install QGIS, or set "
        "QGIS_MCP_WORKFLOWS_QGIS_LAUNCHER to its python-qgis(-ltr).bat / bundled python3"
    ),
)


class FakeExecutor:
    """Records dispatch calls and returns scripted responses.

    Usage:
        fake = FakeExecutor()
        fake.responses["add_vector_layer"] = {"layer_id": "L1", ...}
        # ...run tool that calls dispatch...
        assert fake.calls[0] == ("add_vector_layer", {"path": "..."})
    """

    def __init__(self) -> None:
        self.responses: dict[str, Any | Callable[[dict | None], Any]] = {}
        self.calls: list[tuple[str, dict | None]] = []

    def dispatch(self, command: str, params: dict | None = None, timeout: int | None = None) -> Any:
        self.calls.append((command, params))
        if command not in self.responses:
            raise AssertionError(
                f"FakeExecutor: unexpected command {command!r}. "
                f"Set fake.responses[{command!r}] = ... in your test."
            )
        resp = self.responses[command]
        if callable(resp):
            return resp(params)
        return resp


@pytest.fixture
def fake_executor() -> FakeExecutor:
    """Install a FakeExecutor as the active executor; reset on teardown."""
    from qgis_mcp_workflows import executors

    fake = FakeExecutor()
    executors.set_executor(fake)
    try:
        yield fake
    finally:
        executors.set_executor(None)
