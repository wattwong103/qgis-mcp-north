"""Transport selection at server startup (``server._build_executor``).

No QGIS needed: the plugin probe and the headless constructor are patched.
"""

from __future__ import annotations

import pytest

from qgis_mcp_workflows import server
from qgis_mcp_workflows.errors import HeadlessUnavailableError, TransportUnavailableError
from qgis_mcp_workflows.executors.plugin import PluginExecutor


class _FakeHeadless:
    def __init__(self) -> None:
        pass

    def dispatch(self, command, params=None, timeout=None):
        return {"pong": True}


def _headless_unavailable():
    # The real macOS detail carries its own "Next:" hint; it must not leak into
    # the combined message (that hint names a transport= argument tools lack).
    raise HeadlessUnavailableError(
        "no QGIS.app found under /Applications. "
        "Next: qgis_render_map(..., transport='plugin') if QGIS Desktop is already running."
    )


@pytest.fixture(autouse=True)
def default_plugin_address(monkeypatch):
    monkeypatch.delenv("QGIS_MCP_WORKFLOWS_HOST", raising=False)
    monkeypatch.delenv("QGIS_MCP_WORKFLOWS_PORT", raising=False)


@pytest.fixture
def no_headless(monkeypatch):
    monkeypatch.setattr(
        "qgis_mcp_workflows.executors.headless.HeadlessExecutor", _headless_unavailable
    )


def test_auto_prefers_plugin_when_port_open(monkeypatch):
    monkeypatch.setattr(server, "_plugin_reachable", lambda host, port: True)
    executor, chosen = server._build_executor("auto")
    assert isinstance(executor, PluginExecutor)
    assert chosen == "plugin"


def test_auto_falls_back_to_headless(monkeypatch):
    monkeypatch.setattr(server, "_plugin_reachable", lambda host, port: False)
    monkeypatch.setattr(
        "qgis_mcp_workflows.executors.headless.HeadlessExecutor", _FakeHeadless
    )
    executor, chosen = server._build_executor("auto")
    assert isinstance(executor, _FakeHeadless)
    assert chosen == "headless"


def test_auto_starts_degraded_when_nothing_available(monkeypatch, no_headless):
    """Startup must not raise: an exception here closes the MCP connection,
    so the client sees "connection closed" instead of a readable error."""
    monkeypatch.setattr(server, "_plugin_reachable", lambda host, port: False)
    executor, chosen = server._build_executor("auto")
    assert chosen == "unavailable"
    with pytest.raises(TransportUnavailableError) as exc:
        executor.dispatch("ping", {})
    msg = str(exc.value)
    assert "9877" in msg  # the plugin port that was probed
    assert "no QGIS.app found" in msg  # the headless reason, verbatim
    assert msg.count("Next:") == 1
    assert msg.rstrip().endswith("Next: qgis_ping() once QGIS is open.")


def test_degraded_executor_recovers_when_plugin_comes_up(monkeypatch, no_headless):
    """Opening QGIS Desktop after the server started must not need a restart."""
    reachable = {"up": False}
    monkeypatch.setattr(server, "_plugin_reachable", lambda host, port: reachable["up"])
    executor, _ = server._build_executor("auto")

    sent = []
    monkeypatch.setattr(
        PluginExecutor,
        "dispatch",
        lambda self, command, params=None, timeout=None: sent.append(command) or {"pong": True},
    )
    from qgis_mcp_workflows.executors import get_executor, set_executor

    reachable["up"] = True
    try:
        assert executor.dispatch("ping", {}) == {"pong": True}
        assert sent == ["ping"]
        # Later calls (and qgis_ping's transport name) go straight to the plugin.
        assert isinstance(get_executor(), PluginExecutor)
    finally:
        set_executor(None)


def test_degraded_transport_name(monkeypatch, no_headless):
    from qgis_mcp_workflows.executors import set_executor

    monkeypatch.setattr(server, "_plugin_reachable", lambda host, port: False)
    executor, _ = server._build_executor("auto")
    set_executor(executor)
    try:
        assert server._executor_transport_name() == "unavailable"
    finally:
        set_executor(None)


def test_explicit_headless_still_fails_fast(no_headless):
    """--transport=headless is an explicit request: keep the loud startup error."""
    with pytest.raises(HeadlessUnavailableError):
        server._build_executor("headless")


def test_degraded_executor_swaps_once(monkeypatch, no_headless):
    """Tools that hold the executor in a local and dispatch several times
    (load, then set CRS, then clean up) must not re-probe and re-swap per call."""
    from qgis_mcp_workflows.executors import set_executor

    probes = []
    monkeypatch.setattr(
        server, "_plugin_reachable", lambda host, port: probes.append(1) or True
    )
    monkeypatch.setattr(
        PluginExecutor, "dispatch", lambda self, command, params=None, timeout=None: {}
    )
    swaps = []
    monkeypatch.setattr(
        "qgis_mcp_workflows.executors.set_executor", lambda ex: swaps.append(ex)
    )
    executor = server.UnavailableExecutor("localhost", 9877, "detail")
    try:
        for command in ("add_vector_layer", "set_layer_crs", "remove_layer"):
            executor.dispatch(command, {})
        assert len(probes) == 1
        assert len(swaps) == 1
    finally:
        set_executor(None)
