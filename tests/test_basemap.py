"""Tests for basemap tile support on render tools (live XYZ presets).

Server-side unit tests with the mocked executor. The plugin-side PyQGIS
(QgsRasterLayer type=xyz, EPSG:3857 reprojection) is verified live against a
running QGIS instance, not here — there is no QGIS in the unit-test process.

What these lock down:
- preset → basemap_spec resolution (the wire shape sent to the plugin)
- "none" preserves the legacy no-basemap behavior (back-compat)
- the spec is threaded into the dispatch params untouched
- provider attribution round-trips back into the response model
"""

from __future__ import annotations

from typing import get_args

import pytest

from qgis_mcp_workflows.server import (
    _BASEMAP_ALIASES,
    _BASEMAP_PRESETS,
    BasemapName,
    _resolve_basemap,
    qgis_list_basemaps,
    qgis_render_choropleth,
)


def _ok_response() -> dict:
    return {
        "output_path": "/tmp/x.png",
        "width": 1600, "height": 1200, "dpi": 150,
        "extent": [15555000.0, 4256000.0, 15566000.0, 4263000.0],
        "crs": "EPSG:3857", "n_layers": 2,
        "field": "total_trips", "n_classes": 5,
        "breaks": [0.0, 100.0, 500.0, 1000.0, 5000.0, 10000.0],
        "mode": "quantile",
        "min_value": 0.0, "max_value": 9876.0,
        "n_features": 4, "n_matched": 4, "n_unmatched": 0,
        "basemap_attribution": "© OpenStreetMap contributors © CARTO",
        "basemap_source": "positron (live xyz)",
    }


def test_resolve_basemap_none_returns_none():
    assert _resolve_basemap("none", 1.0) is None


def test_resolve_basemap_returns_xyz_spec():
    spec = _resolve_basemap("light", 0.85)
    assert spec["kind"] == "xyz"
    assert spec["attribution"]  # non-empty provider credit
    assert spec["opacity"] == 0.85
    assert spec["zmin"] == 0
    assert spec["zmax"] >= 1


def test_resolve_basemap_imagery_present():
    spec = _resolve_basemap("imagery", 1.0)
    assert "World_Imagery" in spec["url"]
    assert spec["attribution"]


def test_no_preset_uses_the_carto_cdn():
    """Regression: CARTO put basemaps.cartocdn.com behind an API key.

    Tiles still return HTTP 200 and a valid PNG — every pixel just carries an
    "API KEY REQUIRED" watermark — so nothing downstream can detect the failure.
    Three presets pointed there and silently produced watermarked figures.
    """
    for name in _BASEMAP_PRESETS:
        assert "cartocdn" not in _resolve_basemap(name, 1.0)["url"], name


def test_every_preset_carries_attribution():
    """A basemap licence generally requires the credit to travel with the image."""
    for name in _BASEMAP_PRESETS:
        assert _resolve_basemap(name, 1.0)["attribution"].strip(), name


def test_deprecated_aliases_resolve_to_canonical_presets():
    """Old CARTO-era names keep working; the response reports what was drawn."""
    for alias, canonical in _BASEMAP_ALIASES.items():
        spec = _resolve_basemap(alias, 1.0)
        assert canonical in _BASEMAP_PRESETS, canonical
        assert spec["name"] == canonical, alias
        assert spec["url"] == _BASEMAP_PRESETS[canonical][0]


def test_basemap_literal_matches_the_presets_and_aliases():
    """The tool's enum must offer exactly what _resolve_basemap accepts."""
    declared = set(get_args(BasemapName))
    assert declared == {"none"} | set(_BASEMAP_PRESETS) | set(_BASEMAP_ALIASES)


def test_choropleth_default_sends_no_basemap_spec(fake_executor):
    fake_executor.responses["render_choropleth"] = _ok_response()
    qgis_render_choropleth(
        zones_path="/z.shp", value_field="population", output_png="/o.png"
    )
    params = fake_executor.calls[0][1]
    assert params["basemap_spec"] is None


def test_choropleth_basemap_threads_spec_into_params(fake_executor):
    fake_executor.responses["render_choropleth"] = _ok_response()
    qgis_render_choropleth(
        zones_path="/z.shp", value_field="population", output_png="/o.png",
        basemap="positron", basemap_opacity=0.85,
    )
    spec = fake_executor.calls[0][1]["basemap_spec"]
    assert spec is not None and spec["kind"] == "xyz"
    assert spec["opacity"] == 0.85
    assert spec["name"] == "light"  # alias resolved before dispatch


def test_choropleth_response_carries_attribution(fake_executor):
    fake_executor.responses["render_choropleth"] = _ok_response()
    result = qgis_render_choropleth(
        zones_path="/z.shp", value_field="population", output_png="/o.png",
        basemap="positron",
    )
    assert result.basemap_attribution == "© OpenStreetMap contributors © CARTO"
    assert result.basemap_source == "positron (live xyz)"


# ── QuickMapServices surface ───────────────────────────────────────────────


def test_qms_reference_is_passed_through_unresolved():
    """The catalog lives in the QGIS profile, which may be on another host.

    Resolving MCP-side would read the wrong profile (or none) under
    --transport=plugin against a remote QGIS, so the plugin resolves it.
    """
    spec = _resolve_basemap("qms:opentopomap", 0.7)
    assert spec == {"kind": "qms", "id": "opentopomap", "opacity": 0.7}


def test_qms_reference_strips_surrounding_whitespace():
    assert _resolve_basemap("qms:  esri_gray_light  ", 1.0)["id"] == "esri_gray_light"


def test_empty_qms_reference_is_rejected():
    from qgis_mcp_workflows.errors import BasemapNotFoundError

    with pytest.raises(BasemapNotFoundError):
        _resolve_basemap("qms:", 1.0)


def test_unknown_basemap_names_an_alternative():
    from qgis_mcp_workflows.errors import BasemapNotFoundError

    with pytest.raises(BasemapNotFoundError) as exc:
        _resolve_basemap("positrn", 1.0)
    msg = str(exc.value)
    assert "light" in msg and "qms:<id>" in msg and "Next:" in msg


def test_choropleth_threads_qms_spec_into_params(fake_executor):
    fake_executor.responses["render_choropleth"] = _ok_response()
    qgis_render_choropleth(
        zones_path="/z.shp", value_field="population", output_png="/o.png",
        basemap="qms:opentopomap", basemap_opacity=0.5,
    )
    spec = fake_executor.calls[0][1]["basemap_spec"]
    assert spec == {"kind": "qms", "id": "opentopomap", "opacity": 0.5}


def test_list_basemaps_reports_catalog(fake_executor):
    fake_executor.responses["list_basemaps"] = {
        "presets": ["light", "dark", "streets", "imagery"],
        "qms": [{"id": "opentopomap", "alias": "OpenTopoMap", "group": "openstreetmap",
                 "zmin": 0, "zmax": 17, "attribution": "© OSM", "licence": "CC-BY-SA"}],
        "n_qms": 1,
        "qms_rejected": [{"id": "google_road", "reason": "licence", "detail": "..."}],
    }
    result = qgis_list_basemaps()
    assert result.presets == ["light", "dark", "streets", "imagery"]
    assert result.n_qms == 1
    assert result.qms_rejected[0]["reason"] == "licence"
    assert result.qms_error is None


def test_list_basemaps_surfaces_missing_quickmapservices(fake_executor):
    """Presets must still be usable when QMS isn't installed."""
    fake_executor.responses["list_basemaps"] = {
        "presets": ["light", "dark", "streets", "imagery"],
        "qms": [], "n_qms": 0, "qms_rejected": [],
        "qms_error": "QuickMapServices is not installed. Next: retry with basemap='light'.",
    }
    result = qgis_list_basemaps()
    assert result.presets and result.n_qms == 0
    assert "Next:" in result.qms_error
