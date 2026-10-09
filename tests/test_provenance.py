"""Unit tests for figure provenance (TASK-12). No QGIS needed."""

from __future__ import annotations

import hashlib
import inspect
import os
import typing

import pytest
from pydantic import BaseModel

from qgis_mcp_workflows import executors, provenance


def test_executor_requests_counts_get_executor_calls(fake_executor):
    before = executors.executor_requests()
    executors.get_executor()
    executors.get_executor()
    assert executors.executor_requests() == before + 2


# --- Task 2: portable paths ---------------------------------------------------


def test_normalise_uses_forward_slashes_and_absolute(monkeypatch, tmp_path):
    # chdir first: os.path.relpath raises across Windows drives (C: temp vs H: repo).
    monkeypatch.chdir(tmp_path)
    expected = os.path.realpath(str(tmp_path / "a" / "b.csv")).replace("\\", "/")
    assert provenance.normalise(os.path.join("a", "b.csv")).casefold() == expected.casefold()


def test_portable_under_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    stored, reason = provenance.portable(str(tmp_path / "gufm" / "fig.png"))
    assert stored == "${DROPBOX_ROOT}/gufm/fig.png"
    assert reason is None


def test_portable_matches_whole_components_only(monkeypatch, tmp_path):
    root = tmp_path / "Dropbox"
    monkeypatch.setenv("DROPBOX_ROOT", str(root))
    stored, reason = provenance.portable(str(tmp_path / "Dropbox-old" / "x.png"))
    assert stored == provenance.normalise(str(tmp_path / "Dropbox-old" / "x.png"))
    assert reason == "outside DROPBOX_ROOT"


def test_portable_unset_root_is_machine_specific(monkeypatch, tmp_path):
    monkeypatch.delenv("DROPBOX_ROOT", raising=False)
    stored, reason = provenance.portable(str(tmp_path / "x.png"))
    assert stored == provenance.normalise(str(tmp_path / "x.png"))
    assert reason == "DROPBOX_ROOT unset"


@pytest.mark.parametrize("insensitive, expected", [(True, "${DROPBOX_ROOT}/x.png"), (False, None)])
def test_portable_case_rule(monkeypatch, tmp_path, insensitive, expected):
    monkeypatch.setattr(provenance, "_case_insensitive", lambda: insensitive)
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path / "Root"))
    stored, _ = provenance.portable(str(tmp_path / "ROOT" / "x.png"))
    if expected is None:
        assert not stored.startswith("${DROPBOX_ROOT}")
    else:
        assert stored == expected


# --- Task 3: fingerprints -----------------------------------------------------


def test_fingerprint_hashes_small_file(tmp_path):
    f = tmp_path / "zones.gpkg"
    f.write_bytes(b"abc")
    fp = provenance.fingerprint(str(f))
    assert fp["sha256"] == hashlib.sha256(b"abc").hexdigest()
    assert fp["hashed"] is True and fp["bytes"] == 3 and fp["missing"] is False
    assert fp["mtime"].endswith("Z")


def test_fingerprint_above_cap_is_not_hashed(monkeypatch, tmp_path):
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_PROVENANCE_HASH_MAX_MB", "0.000001")  # ~1 byte
    f = tmp_path / "big.duckdb"
    f.write_bytes(b"0123456789")
    fp = provenance.fingerprint(str(f))
    assert fp["sha256"] is None and fp["hashed"] is False and fp["bytes"] == 10


def test_fingerprint_missing_file(tmp_path):
    fp = provenance.fingerprint(str(tmp_path / "nope.csv"))
    assert fp["missing"] is True and fp["bytes"] is None and fp["sha256"] is None


def test_fingerprint_cache_avoids_second_hash(monkeypatch, tmp_path):
    provenance.reset_for_tests()
    f = tmp_path / "a.csv"
    f.write_bytes(b"x")
    provenance.fingerprint(str(f))
    hashed = []
    real_sha256 = provenance.hashlib.sha256
    monkeypatch.setattr(provenance.hashlib, "sha256", lambda *a: (hashed.append(1), real_sha256(*a))[1])
    assert provenance.fingerprint(str(f))["sha256"] == real_sha256(b"x").hexdigest()
    assert hashed == []


def test_snapshot_changes_when_file_changes(tmp_path):
    f = tmp_path / "a.csv"
    f.write_bytes(b"x")
    before = provenance.snapshot(str(f))
    f.write_bytes(b"xy")
    assert provenance.snapshot(str(f)) != before
    assert provenance.snapshot(str(tmp_path / "none")) is None


# --- Task 4: classify inputs, extract written files ----------------------------


def _tool_functions():
    from qgis_mcp_workflows import compound, server

    for module in (server, compound):
        for name, fn in vars(module).items():
            if name.startswith("qgis_") and inspect.isfunction(fn) and fn.__module__ == module.__name__:
                yield fn


def test_every_path_like_parameter_is_classified():
    known = (provenance.INPUT_ARGS | provenance.INPUT_LIST_ARGS
             | provenance.OUTPUT_ARGS | provenance.NOT_PATH_ARGS)
    missing = sorted(
        f"{fn.__name__}({param})"
        for fn in _tool_functions()
        for param in inspect.signature(fn).parameters
        if any(h in param for h in provenance.PATH_HINTS) and param not in known
    )
    assert missing == []


def _result_models():
    for fn in _tool_functions():
        ret = typing.get_type_hints(fn).get("return")
        for cls in (typing.get_args(ret) or (ret,)):
            if isinstance(cls, type) and issubclass(cls, BaseModel):
                yield cls


def test_every_result_model_with_a_path_field_has_an_extractor():
    path_fields = {"output_path", "output_csv", "pptx_path", "files", "manifest"}
    uncovered = sorted({
        cls.__name__ for cls in _result_models()
        if path_fields & set(cls.model_fields)
        and not any(c.__name__ in provenance.WRITTEN_BY_MODEL for c in cls.__mro__)
    })
    assert uncovered == []


def test_input_paths_flattens_lists_and_skips_empty(tmp_path):
    args = {"zones_path": str(tmp_path / "z.gpkg"), "trajectory_csvs": ["a.csv", "b.csv"],
            "value_csv": None, "output_png": "o.png", "palette": "gufm"}
    names = [name for name, _ in provenance.input_paths(args)]
    assert names == ["zones_path", "trajectory_csvs", "trajectory_csvs"]


def test_implicit_bundled_deck_template():
    found = provenance.implicit_input_paths("qgis_figures_to_pptx", {"template_pptx": None})
    assert [name for name, _ in found] == ["template_pptx (bundled)"]
    assert provenance.implicit_input_paths("qgis_figures_to_pptx", {"template_pptx": "t.pptx"}) == []


def test_written_files_atlas_uses_files_not_directory(tmp_path):
    from qgis_mcp_workflows.server import AtlasExportResult

    pages = [tmp_path / "a.png", tmp_path / "b.png"]
    for p in pages:
        p.write_bytes(b"png")
    result = AtlasExportResult(output_dir=str(tmp_path), output_path=str(tmp_path), format="png",
                               n_pages=2, layout_name="L", files=[str(p) for p in pages])
    assert provenance.written_files(result) == [provenance.normalise(str(p)) for p in pages]


def test_written_files_skips_missing_outputs(tmp_path):
    from qgis_mcp_workflows.server import ExportResult

    result = ExportResult(output_path=str(tmp_path / "never.pdf"), format="pdf", n_pages=1, layout_name="L")
    assert provenance.written_files(result) == []


def test_result_summary_keeps_scalars_drops_paths():
    from qgis_mcp_workflows.server import ExportResult

    summary = provenance.result_summary(
        ExportResult(output_path="/x.pdf", format="pdf", n_pages=3, layout_name="L"))
    # Subset, not equality: #25 adds unavailable_layers ([] — a short scalar list, kept).
    assert {"format": "pdf", "n_pages": 3, "layout_name": "L"}.items() <= summary.items()
    assert "output_path" not in summary
