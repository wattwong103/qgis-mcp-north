"""Unit tests for figure provenance (TASK-12). No QGIS needed."""

from __future__ import annotations

from qgis_mcp_workflows import executors


def test_executor_requests_counts_get_executor_calls(fake_executor):
    before = executors.executor_requests()
    executors.get_executor()
    executors.get_executor()
    assert executors.executor_requests() == before + 2
