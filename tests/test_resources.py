"""MCP resources — FakeExecutor, no QGIS required."""

from __future__ import annotations

import json

from qgis_mcp_workflows.server import resource_basemaps, resource_project, resource_status


def test_resource_status_healthy(fake_executor):
    fake_executor.responses["diagnose"] = {
        "status": "healthy",
        "checks": [{"name": "plugin_version", "status": "ok", "detail": "1.10.0"}],
    }
    payload = json.loads(resource_status())
    assert payload["status"] in ("healthy", "degraded")
    assert payload["transport"] == "fake"


def test_resource_project(fake_executor):
    fake_executor.responses["get_project_info"] = {
        "filename": "/tmp/x.qgz",
        "title": "demo",
        "layer_count": 2,
        "crs": "EPSG:4326",
        "layers": [],
    }
    payload = json.loads(resource_project())
    assert payload["filename"] == "/tmp/x.qgz"
    assert payload["layer_count"] == 2


def test_resource_basemaps(fake_executor):
    fake_executor.responses["list_basemaps"] = {
        "presets": ["light", "dark"],
        "qms": [],
        "n_qms": 0,
        "qms_rejected": [],
        "qms_error": None,
    }
    payload = json.loads(resource_basemaps())
    assert "light" in payload["presets"]
