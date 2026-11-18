"""Transport selection at server startup (``server._build_executor``).

No QGIS needed: the plugin probe, the desktop-process check, and the headless
constructor are patched.
"""

from __future__ import annotations

import pytest

from qgis_mcp_workflows import server
from qgis_mcp_workflows.errors import (
    HeadlessUnavailableError,
    PluginNotStartedError,
    TransportUnavailableError,
)
from qgis_mcp_workflows.executors.plugin import PluginExecutor


class _FakeHeadless:
    def __init__(self) -> None:
        pass

    def dispatch(self, command, params=None, timeout=None):
        return {"pong": True, "command": command}


def _headless_unavailable():
    # The real launcher detail may carry its own "Next:" hint; it must not leak
    # into the combined message (that hint would name a second recovery).
    raise HeadlessUnavailableError(
        "no QGIS.app found under /Applications. "
        "Next: qgis_render_map(..., transport='plugin') if QGIS Desktop is already running."
    )


@pytest.fixture(autouse=True)
def default_plugin_address(monkeypatch):
    monkeypatch.delenv("QGIS_MCP_WORKFLOWS_HOST", raising=False)
    monkeypatch.delenv("QGIS_MCP_WORKFLOWS_PORT", raising=False)
    monkeypatch.delenv("QGIS_MCP_WORKFLOWS_TRANSPORT", raising=False)
    monkeypatch.setattr(server, "_qgis_desktop_running", lambda: False)
    # A developer config.toml must not change these tests.
    monkeypatch.setattr(server, "config_path", _missing_config)


def _missing_config():
    from pathlib import Path

    return Path("/no/such/qgis-mcp-workflows/config.toml")


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


def test_auto_falls_back_to_headless_when_desktop_is_not_running(monkeypatch):
    monkeypatch.setattr(server, "_plugin_reachable", lambda host, port: False)
    monkeypatch.setattr(
        "qgis_mcp_workflows.executors.headless.HeadlessExecutor", _FakeHeadless
    )
    executor, chosen = server._build_executor("auto")
    assert chosen == "auto"
    assert isinstance(executor, server.AutoExecutor)
    assert executor.dispatch("ping", {}) == {"pong": True, "command": "ping"}
    assert isinstance(executor._headless, _FakeHeadless)
    assert executor.backend() == "headless"


def test_auto_does_not_render_headless_while_qgis_desktop_is_open(monkeypatch):
    """Desktop is open and Run MCP was not clicked: fail, do not draw headless."""
    monkeypatch.setattr(server, "_plugin_reachable", lambda host, port: False)
    monkeypatch.setattr(server, "_qgis_desktop_running", lambda: True)
    constructed = []

    class _Boom:
        def __init__(self) -> None:
            constructed.append(1)

    monkeypatch.setattr("qgis_mcp_workflows.executors.headless.HeadlessExecutor", _Boom)
    executor, chosen = server._build_executor("auto")
    assert chosen == "auto"
    with pytest.raises(PluginNotStartedError) as exc:
        executor.dispatch("render_layers_to_path", {})
    msg = str(exc.value)
    assert "Run MCP was not started" in msg
    assert "Auto-start" in msg
    assert msg.count("Next:") == 1
    assert constructed == []


def test_auto_switches_to_plugin_once_run_mcp_is_clicked(monkeypatch):
    """Opening the socket after startup must not need an MCP restart."""
    reachable = {"up": False}
    monkeypatch.setattr(server, "_plugin_reachable", lambda host, port: reachable["up"])
    monkeypatch.setattr(server, "_qgis_desktop_running", lambda: True)
    monkeypatch.setattr(
        "qgis_mcp_workflows.executors.headless.HeadlessExecutor", _headless_unavailable
    )
    executor, _ = server._build_executor("auto")
    with pytest.raises(PluginNotStartedError):
        executor.dispatch("ping", {})

    sent = []
    monkeypatch.setattr(
        PluginExecutor,
        "dispatch",
        lambda self, command, params=None, timeout=None: sent.append(command) or {"pong": True},
    )
    reachable["up"] = True
    assert executor.dispatch("ping", {}) == {"pong": True}
    assert sent == ["ping"]
    assert executor.backend() == "plugin"


def test_auto_starts_degraded_when_nothing_available(monkeypatch, no_headless):
    """Startup must not raise: an exception here closes the MCP connection,
    so the client sees "connection closed" instead of a readable error."""
    monkeypatch.setattr(server, "_plugin_reachable", lambda host, port: False)
    executor, chosen = server._build_executor("auto")
    assert chosen == "auto"
    with pytest.raises(TransportUnavailableError) as exc:
        executor.dispatch("ping", {})
    msg = str(exc.value)
    assert "9877" in msg  # the plugin port that was probed
    assert "no QGIS.app found" in msg  # the headless reason, verbatim
    assert "Run MCP" in msg
    assert msg.count("Next:") == 1
    assert msg.rstrip().endswith("Next: qgis_ping() once QGIS is open.")


def test_auto_stops_using_headless_if_desktop_opens_later(monkeypatch):
    """A headless fallback must not keep drawing after the user opens QGIS."""
    desktop = {"up": False}
    monkeypatch.setattr(server, "_plugin_reachable", lambda host, port: False)
    monkeypatch.setattr(server, "_qgis_desktop_running", lambda: desktop["up"])
    monkeypatch.setattr(
        "qgis_mcp_workflows.executors.headless.HeadlessExecutor", _FakeHeadless
    )
    executor, _ = server._build_executor("auto")
    assert executor.dispatch("ping", {})["pong"] is True
    desktop["up"] = True
    with pytest.raises(PluginNotStartedError, match="Run MCP was not started"):
        executor.dispatch("render_layers_to_path", {})


def test_explicit_headless_still_fails_fast(no_headless):
    """--transport=headless is an explicit request: keep the loud startup error."""
    with pytest.raises(HeadlessUnavailableError) as exc:
        server._build_executor("headless")
    msg = str(exc.value)
    assert msg.count("Next:") == 1
    assert "--transport=headless" in msg
    assert "--transport=plugin" not in msg


def test_auto_reuses_one_plugin_executor(monkeypatch):
    """Repeated dispatches while the socket stays up share one plugin executor."""
    monkeypatch.setattr(server, "_plugin_reachable", lambda host, port: True)
    constructed = []

    class _Counting(PluginExecutor):
        def __init__(self, host, port):
            constructed.append((host, port))

        def dispatch(self, command, params=None, timeout=None):
            return {}

    monkeypatch.setattr("qgis_mcp_workflows.executors.plugin.PluginExecutor", _Counting)
    executor = server.AutoExecutor("localhost", 9877)
    for command in ("add_vector_layer", "set_layer_crs", "remove_layer"):
        executor.dispatch(command, {})
    assert constructed == [("localhost", 9877)]


def test_desktop_process_names():
    assert server._is_qgis_desktop_process("qgis-bin")
    assert server._is_qgis_desktop_process("QGIS")
    assert server._is_qgis_desktop_process(r"C:\OSGeo4W\bin\qgis-ltr-bin.exe")
    assert not server._is_qgis_desktop_process("qgis-mcp-workflows-server")
    assert not server._is_qgis_desktop_process("python")
    assert not server._is_qgis_desktop_process("python3")


def test_config_toml_sets_transport_when_env_and_cli_are_unset(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('transport = "headless"\nhost = "127.0.0.1"\nport = 9999\n', encoding="utf-8")
    monkeypatch.setattr(server, "config_path", lambda: cfg)
    assert server.resolve_transport(None) == ("headless", str(cfg))
    assert server.resolve_endpoint() == ("127.0.0.1", 9999)


def test_env_beats_config_toml(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('transport = "headless"\nport = 9999\n', encoding="utf-8")
    monkeypatch.setattr(server, "config_path", lambda: cfg)
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_TRANSPORT", "plugin")
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_PORT", "9877")
    assert server.resolve_transport(None)[0] == "plugin"
    assert server.resolve_endpoint()[1] == 9877


def test_cli_beats_env(monkeypatch):
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_TRANSPORT", "headless")
    assert server.resolve_transport("plugin") == ("plugin", "--transport")


def test_broken_config_toml_is_ignored(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text("transport = [\n", encoding="utf-8")
    monkeypatch.setattr(server, "config_path", lambda: cfg)
    assert server.resolve_transport(None) == ("auto", "default")


def test_headless_error_next_matches_headless():
    err = HeadlessUnavailableError("no launcher")
    assert "Next: fix that launcher path, then restart the server with --transport=headless." in str(err)
    assert "--transport=plugin" not in str(err)
