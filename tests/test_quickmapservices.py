"""QuickMapServices catalog reading and basemap_spec resolution.

No QGIS needed: :func:`catalog` and :func:`resolve` take an explicit profile
directory, so these build a miniature QMS tree on disk and read it back. The
only QGIS touch point is the default profile lookup, which these never hit.
"""

from __future__ import annotations

import pytest

from qgis_mcp_workflows_plugin.quickmapservices import (
    QmsSourceError,
    QmsUnavailableError,
    catalog,
    resolve,
)

CONTRIB = ("python", "plugins", "quick_map_services", "quickmapservices_contrib", "data_sources")


def _write(profile, parts, ds_id, body):
    d = profile.joinpath(*parts) / ds_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.ini").write_text(body, encoding="utf-8")


def _tms(ds_id, url, group="openstreetmap", extra_tms="", licence=True, type_="TMS"):
    lic = (
        "[license]\nname = CC-BY-SA 2.0\ncopyright_text =© OpenStreetMap contributors\n"
        "terms_of_use = https://example.invalid/tos\n"
    ) if licence else ""
    return (
        f"[general]\nid = {ds_id}\ntype = {type_}\n\n"
        f"[ui]\ngroup = {group}\nalias = {ds_id.title()}\n\n"
        f"{lic}\n[tms]\nurl = {url}\n{extra_tms}"
    )


@pytest.fixture
def profile(tmp_path):
    """A miniature QGIS profile with a representative QMS catalog."""
    _write(tmp_path, CONTRIB, "osm_mapnik",
           _tms("osm_mapnik", "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                extra_tms="zmin = 0\nzmax = 19\ny_origin_top = 1\nepsg_crs_id = 3857\n"))
    _write(tmp_path, CONTRIB, "esri_gray_light",
           _tms("esri_gray_light", "https://example.invalid/{z}/{y}/{x}",
                group="esri", extra_tms="zmax = 20\n", licence=False))
    # bottom-origin TMS: QMS rewrites {y} -> {-y}
    _write(tmp_path, CONTRIB, "flipped_src",
           _tms("flipped_src", "https://example.invalid/{z}/{x}/{y}.png",
                extra_tms="y_origin_top = 0\n"))
    # query string: '=' and '&' must be percent-escaped or the URI is corrupted
    _write(tmp_path, CONTRIB, "query_src",
           _tms("query_src", "https://example.invalid/t?x={x}&y={y}&z={z}&style=grey"))
    # rejected: non-3857 CRS
    _write(tmp_path, CONTRIB, "yandex_satellite",
           _tms("yandex_satellite", "https://example.invalid/{z}/{x}/{y}",
                group="yandex", extra_tms="epsg_crs_id = 3395\n"))
    # rejected: licence-restricted provider
    _write(tmp_path, CONTRIB, "google_road",
           _tms("google_road", "https://example.invalid/{z}/{x}/{y}", group="google"))
    # ignored: not a tile service
    _write(tmp_path, CONTRIB, "some_wms",
           _tms("some_wms", "https://example.invalid/wms", type_="WMS"))
    return tmp_path


# ── catalog ────────────────────────────────────────────────────────────────


def test_catalog_lists_only_renderable_sources(profile):
    ids = [e["id"] for e in catalog(profile=str(profile))]
    assert ids == ["esri_gray_light", "flipped_src", "osm_mapnik", "query_src"]


def test_catalog_rejects_non_3857_with_a_reason(profile):
    _, rejected = catalog(profile=str(profile), include_rejected=True)
    hit = [r for r in rejected if r[0] == "yandex_satellite"]
    assert hit and hit[0][1] == "crs" and "3395" in hit[0][2]


def test_catalog_rejects_restricted_providers_with_a_reason(profile):
    _, rejected = catalog(profile=str(profile), include_rejected=True)
    hit = [r for r in rejected if r[0] == "google_road"]
    assert hit and hit[0][1] == "licence" and "Google" in hit[0][2]


def test_catalog_applies_qms_zoom_defaults(profile):
    by_id = {e["id"]: e for e in catalog(profile=str(profile))}
    assert (by_id["osm_mapnik"]["zmin"], by_id["osm_mapnik"]["zmax"]) == (0, 19)
    # QMS defaults when the INI omits them
    assert (by_id["flipped_src"]["zmin"], by_id["flipped_src"]["zmax"]) == (0, 18)


def test_missing_quickmapservices_raises_actionable_error(tmp_path):
    with pytest.raises(QmsUnavailableError) as exc:
        catalog(profile=str(tmp_path))
    assert "Next:" in str(exc.value)


def test_user_sources_shadow_contrib_by_id(profile):
    _write(profile, ("QuickMapServices", "User"), "osm_mapnik",
           _tms("osm_mapnik", "https://user-override.invalid/{z}/{x}/{y}.png"))
    by_id = {e["id"]: e for e in catalog(profile=str(profile))}
    assert "user-override" in by_id["osm_mapnik"]["url"]


# ── resolve ────────────────────────────────────────────────────────────────


def test_resolve_builds_a_basemap_spec(profile):
    spec = resolve("osm_mapnik", opacity=0.6, profile=str(profile))
    assert spec["kind"] == "xyz"
    assert spec["source_id"] == "osm_mapnik"
    assert spec["opacity"] == 0.6
    assert spec["zmax"] == 19
    assert spec["attribution"] == "© OpenStreetMap contributors"


def test_resolve_flips_y_for_bottom_origin_schemes(profile):
    """y_origin_top = 0 means bottom-origin; unflipped it draws upside down."""
    assert "{-y}" in resolve("flipped_src", profile=str(profile))["url"]
    assert "{-y}" not in resolve("osm_mapnik", profile=str(profile))["url"]


def test_resolve_escapes_url_query_separators(profile):
    """'=' and '&' are the provider-URI separators; unescaped they corrupt it."""
    url = resolve("query_src", profile=str(profile))["url"]
    assert "%3D" in url and "%26" in url
    assert "&y=" not in url


def test_resolve_preserves_empty_attribution_rather_than_inventing_one(profile):
    """QMS ships some entries with a blank licence block; say so, don't guess."""
    assert resolve("esri_gray_light", profile=str(profile))["attribution"] == ""


def test_resolve_unknown_id_suggests_near_matches(profile):
    with pytest.raises(QmsSourceError) as exc:
        resolve("osm_mapnikk", profile=str(profile))
    msg = str(exc.value)
    assert "osm_mapnik" in msg and "Next:" in msg


def test_resolve_restricted_source_explains_the_licence(profile):
    with pytest.raises(QmsSourceError) as exc:
        resolve("google_road", profile=str(profile))
    assert "licence" in str(exc.value) and "Next:" in str(exc.value)


def test_resolve_non_3857_source_explains_the_crs(profile):
    with pytest.raises(QmsSourceError) as exc:
        resolve("yandex_satellite", profile=str(profile))
    assert "3395" in str(exc.value) and "Next:" in str(exc.value)


def test_catalog_explains_non_tms_sources_instead_of_hiding_them(profile):
    """QMS carries MVT/WMS entries that look valid in the QGIS UI.

    Silently omitting them turns "qms:versatiles_colorful" into a bare
    "not found", sending the caller hunting for a typo that isn't there.
    """
    _, rejected = catalog(profile=str(profile), include_rejected=True)
    hit = [r for r in rejected if r[0] == "some_wms"]
    assert hit and hit[0][1] == "unsupported" and "WMS" in hit[0][2]
