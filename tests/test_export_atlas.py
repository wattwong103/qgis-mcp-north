"""qgis_export_atlas — FakeExecutor, no QGIS required."""

from __future__ import annotations

import os

import pytest

from qgis_mcp_workflows.errors import AtlasDisabledError, LayoutNotFoundError
from qgis_mcp_workflows.server import qgis_export_atlas


def test_atlas_dispatches_export_atlas(fake_executor, tmp_path):
    fake_executor.responses["export_atlas"] = {
        "output_dir": str(tmp_path),
        "output_path": str(tmp_path / "atlas_0.png"),
        "format": "png",
        "n_pages": 3,
        "layout_name": "pref_atlas",
        "files": [str(tmp_path / "atlas_0.png"), str(tmp_path / "atlas_1.png")],
    }
    result = qgis_export_atlas(
        qgz_path="/tmp/proj.qgz",
        layout_name="pref_atlas",
        output_dir=str(tmp_path),
    )
    cmd, params = fake_executor.calls[0]
    assert cmd == "export_atlas"
    assert params["layout_name"] == "pref_atlas"
    assert os.path.isabs(params["qgz_path"])
    assert result.n_pages == 3
    assert len(result.files) == 2


def test_atlas_disabled_raises(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import ExecutorError

    def fail(_params):
        raise ExecutorError("export_atlas", "ATLAS_DISABLED: layout 'plain' has no atlas")

    fake_executor.responses["export_atlas"] = fail
    with pytest.raises(AtlasDisabledError, match="plain"):
        qgis_export_atlas(
            qgz_path="/tmp/proj.qgz",
            layout_name="plain",
            output_dir=str(tmp_path),
        )


def test_atlas_missing_layout_raises(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import ExecutorError

    def fail(_params):
        raise ExecutorError("export_atlas", "LAYOUT_NOT_FOUND: 'nope'. Available: []")

    fake_executor.responses["export_atlas"] = fail
    with pytest.raises(LayoutNotFoundError, match="nope"):
        qgis_export_atlas(
            qgz_path="/tmp/proj.qgz",
            layout_name="nope",
            output_dir=str(tmp_path),
        )
