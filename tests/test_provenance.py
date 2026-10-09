"""Unit tests for figure provenance (TASK-12). No QGIS needed."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import threading
import typing
from pathlib import Path

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
        and cls.__name__ not in provenance.NOT_RECORDED_MODELS
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


# --- Task 5: environment block --------------------------------------------------


def test_environment_not_queried_when_tool_did_not_dispatch(fake_executor):
    provenance.reset_for_tests()
    facts = provenance.environment(dispatched=False)
    assert facts["qgis"] == "not queried" and facts["plugin"] == "not queried"
    assert fake_executor.calls == []
    assert {"qgis_mcp_workflows", "git", "git_dirty", "extras", "mcp", "python", "platform"} <= set(facts)


def test_environment_reads_diagnose_and_caches_success(fake_executor):
    provenance.reset_for_tests()
    fake_executor.responses["diagnose"] = {"checks": [
        {"name": "qgis", "status": "ok", "detail": {"qgis_version": "3.40.14-Bratislava"}},
        {"name": "plugin_version", "status": "ok", "detail": "1.14.0"},
    ]}
    facts = provenance.environment(dispatched=True)
    assert (facts["qgis"], facts["plugin"], facts["transport"]) == ("3.40.14-Bratislava", "1.14.0", "fake")
    provenance.environment(dispatched=True)
    assert [c for c, _ in fake_executor.calls] == ["diagnose"]  # cached


def test_environment_never_caches_a_failure(fake_executor):
    provenance.reset_for_tests()

    def down(params):
        raise ConnectionError("degraded")

    fake_executor.responses["diagnose"] = down
    assert provenance.environment(dispatched=True)["qgis"] == "unknown"
    provenance.environment(dispatched=True)
    assert [c for c, _ in fake_executor.calls] == ["diagnose", "diagnose"]


# --- Task 6: atomic sidecar write -----------------------------------------------


def test_write_sidecar_writes_json_and_no_temp(tmp_path):
    fig = tmp_path / "fig.png"
    fig.write_bytes(b"png")
    assert provenance.write_sidecar(str(fig), {"schema": provenance.SCHEMA}) is True
    side = tmp_path / "fig.png.provenance.json"
    assert json.loads(side.read_text(encoding="utf-8")) == {"schema": provenance.SCHEMA}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fig.png", "fig.png.provenance.json"]


def test_write_sidecar_replace_failure_removes_stale(monkeypatch, tmp_path):
    fig = tmp_path / "fig.png"
    fig.write_bytes(b"png")
    stale = tmp_path / "fig.png.provenance.json"
    stale.write_text("{}", encoding="utf-8")

    def locked(src, dst):
        raise PermissionError("Dropbox holds the file")

    monkeypatch.setattr(provenance.os, "replace", locked)
    monkeypatch.setattr(provenance, "_REPLACE_DELAY_S", 0)
    assert provenance.write_sidecar(str(fig), {"schema": "x"}) is False
    assert not stale.exists()                                    # cannot vouch for the new figure
    assert [p.name for p in tmp_path.iterdir()] == ["fig.png"]   # no temp left


def test_write_sidecar_mkstemp_failure(monkeypatch, tmp_path):
    fig = tmp_path / "fig.png"
    fig.write_bytes(b"png")

    def no_space(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(provenance.tempfile, "mkstemp", no_space)
    assert provenance.write_sidecar(str(fig), {"schema": "x"}) is False


# --- Task 7: decorator helpers ----------------------------------------------------


def test_enabled_reads_env_per_call(monkeypatch):
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_PROVENANCE", "0")
    assert provenance.enabled() is False
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_PROVENANCE", "1")
    assert provenance.enabled() is True


def test_seq_unique_across_threads():
    seen: list[int] = []
    lock = threading.Lock()

    def take():
        for _ in range(200):
            value = provenance._next_seq()
            with lock:
                seen.append(value)

    threads = [threading.Thread(target=take) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(seen) == len(set(seen)) == 1600


def test_portable_arguments_serialises_tuples_and_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    out = provenance._portable_arguments(
        {"extent": (1, 2, 3, 4), "zones_path": Path(tmp_path / "z.gpkg"), "palette": "gufm"})
    assert out == {"extent": [1, 2, 3, 4], "zones_path": "${DROPBOX_ROOT}/z.gpkg", "palette": "gufm"}


# --- Final review fixes -------------------------------------------------------------


@pytest.mark.parametrize("value", ["none", "None", "NONE", None, ""])
def test_remote_ignores_the_no_basemap_default(value):
    # Every render tool defaults basemap="none"; apply_defaults() fills it in.
    assert provenance._remote({"basemap": value}, []) == []


def test_remote_records_a_real_basemap():
    flagged: list[dict] = []
    assert provenance._remote({"basemap": "qms:42"}, flagged)[0]["value"] == "qms:42"
    assert flagged and flagged[0]["reason"].startswith("QuickMapServices")


def test_fingerprint_survives_an_unreadable_file(monkeypatch, tmp_path):
    provenance.reset_for_tests()
    f = tmp_path / "locked.gpkg"
    f.write_bytes(b"data")

    def locked(*args, **kwargs):
        raise PermissionError("held by Dropbox")

    monkeypatch.setattr(provenance, "open", locked, raising=False)
    fp = provenance.fingerprint(str(f))
    assert fp["hashed"] is False and fp["sha256"] is None and fp["missing"] is False
    assert "held by Dropbox" in fp["error"]


@pytest.mark.parametrize("raw", ["nan", "inf", "-1", "abc"])
def test_hash_cap_falls_back_on_unusable_values(monkeypatch, raw):
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_PROVENANCE_HASH_MAX_MB", raw)
    assert provenance.hash_cap_bytes() == 256 * 1024 * 1024


@pytest.mark.parametrize("content", ["[]", "null", "{bad json", "[" * 5000 + "]" * 5000],
                         ids=["list", "null", "bad-json", "deep-nesting"])
def test_made_by_ignores_a_malformed_producer_sidecar(tmp_path, content):
    f = tmp_path / "in.csv"
    f.write_bytes(b"x")
    (tmp_path / "in.csv.provenance.json").write_text(content, encoding="utf-8")
    assert provenance._made_by(str(f), hashlib.sha256(b"x").hexdigest()) is None


def test_git_state_failure_is_unknown_not_clean(monkeypatch):
    class Failed:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(provenance.subprocess, "run", lambda *a, **k: Failed())
    assert provenance._git_state() in ({"git": None, "git_dirty": None},)


def test_git_state_takes_no_optional_locks_and_decodes_utf8(monkeypatch):
    seen: list[tuple[list, dict]] = []

    class Ok:
        returncode = 0
        stdout = ""

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return Ok()

    monkeypatch.setattr(provenance.subprocess, "run", run)
    provenance._git_state()
    if not seen:
        pytest.skip("package is not a git checkout here")
    assert all("--no-optional-locks" in argv for argv, _ in seen)
    assert all(kw.get("encoding") == "utf-8" and kw.get("errors") == "replace" for _, kw in seen)


def test_tools_that_never_write_files_are_not_wrapped():
    from qgis_mcp_workflows import server

    assert provenance.with_provenance(server.qgis_layer_inspect) is server.qgis_layer_inspect
    assert provenance.with_provenance(server.qgis_style_categorized) is server.qgis_style_categorized
    assert provenance.with_provenance(server.qgis_render_choropleth) is not server.qgis_render_choropleth


def test_result_summary_flattens_one_level_without_paths():
    from qgis_mcp_workflows.server import ChoroplethResult

    result = ChoroplethResult.model_validate({
        "output_path": "/x.png", "width": 8, "height": 6, "dpi": 150, "extent": [0, 0, 1, 1],
        "crs": "EPSG:4326", "n_layers": 1, "field": "n", "n_classes": 5, "breaks": [1.0, 2.0],
        "mode": "quantile", "min_value": 1.0, "max_value": 2.0, "n_features": 23,
        "join": {"csv": "/data/v.csv", "field": "zone_id", "n_matched": 20, "n_unmatched": 3},
    })
    summary = provenance.result_summary(result)
    assert summary["join.n_matched"] == 20 and summary["join.n_unmatched"] == 3
    assert "join.csv" not in summary and "join" not in summary
