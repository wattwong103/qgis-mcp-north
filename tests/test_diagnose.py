"""qgis_ping / qgis_diagnose — FakeExecutor, no QGIS required."""

from __future__ import annotations

from qgis_mcp_workflows.server import qgis_diagnose, qgis_ping


def test_ping_returns_pong_and_fake_transport(fake_executor):
    fake_executor.responses["ping"] = {"pong": True}
    result = qgis_ping()
    assert result.pong is True
    assert result.transport == "fake"
    assert fake_executor.calls[0][0] == "ping"


def test_diagnose_enriches_version_match(fake_executor):
    fake_executor.responses["diagnose"] = {
        "status": "healthy",
        "checks": [{"name": "plugin_version", "status": "ok", "detail": "1.6.0"}],
    }
    result = qgis_diagnose()
    assert result.transport == "fake"
    names = [c["name"] for c in result.checks]
    assert "plugin_version" in names
    assert "version_match" in names
    match = next(c for c in result.checks if c["name"] == "version_match")
    assert "server" in match["detail"]
    assert match["detail"]["plugin"] == "1.6.0"
