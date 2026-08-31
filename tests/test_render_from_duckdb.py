"""qgis_render_from_duckdb — query a DuckDB file and render the result.

The v0.3 roadmap flagged this for a PFLOW DuckDB store, to skip the CSV
intermediate. These tests build a small synthetic database so they run anywhere
— including CI, which has no PFLOW data. The real stores are listed in
DESIGN.md §10 and the tool is verified against them separately; note they hold
lon/lat columns and no WKT, so the `geometry_column` path is exercised only
here.

Query-side tests use a real DuckDB and a FakeExecutor, so they run anywhere the
duckdb extra is installed. The render-side tests need QGIS and skip without it.
"""

from __future__ import annotations

import math

import pytest

from tests.conftest import requires_headless

duckdb = pytest.importorskip("duckdb", reason="needs the duckdb extra")

from qgis_mcp_workflows.errors import QgisMcpWorkflowsError  # noqa: E402
from qgis_mcp_workflows.server import qgis_render_from_duckdb  # noqa: E402


@pytest.fixture
def db(tmp_path):
    """Zones with WKT polygons, and pings with lon/lat columns."""
    path = tmp_path / "demo.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE zones (zone_id VARCHAR, trips DOUBLE, geom VARCHAR)")
    rows = []
    for i in range(4):
        x0, y0 = 139.68 + 0.03 * (i % 2), 35.68 + 0.03 * (i // 2)
        rows.append((
            f"Z0{i + 1}", float([150, 275, 150, 25][i]),
            f"POLYGON (({x0} {y0}, {x0 + 0.03} {y0}, {x0 + 0.03} {y0 + 0.03}, "
            f"{x0} {y0 + 0.03}, {x0} {y0}))",
        ))
    con.executemany("INSERT INTO zones VALUES (?,?,?)", rows)

    con.execute("CREATE TABLE pings (trip_id VARCHAR, lon DOUBLE, lat DOUBLE, speed DOUBLE)")
    con.executemany("INSERT INTO pings VALUES (?,?,?,?)", [
        (f"T{t % 5}", 139.71 + 0.02 * math.cos(2 * math.pi * t / 200),
         35.705 + 0.02 * math.sin(2 * math.pi * t / 200), 5.0 + (t % 55))
        for t in range(200)
    ])
    con.close()
    return str(path)


def _render_response(geometry_type="Polygon", n=4):
    return {
        "output_path": "/tmp/o.png", "width": 800, "height": 600, "dpi": 96,
        "extent": [139.6, 35.6, 139.8, 35.8], "crs": "EPSG:4326", "n_layers": 1,
        "geometry_type": geometry_type, "n_features": n, "n_skipped": 0,
        "fields": ["zone_id", "trips"], "field": "trips", "breaks": [25.0, 275.0],
        "basemap_attribution": None, "basemap_source": None,
    }


# ── query handling ─────────────────────────────────────────────────────────


def test_wkt_column_becomes_features(fake_executor, db):
    fake_executor.responses["render_wkt_features"] = _render_response()
    qgis_render_from_duckdb(
        db_path=db, query="SELECT zone_id, trips, geom FROM zones",
        output_png="/tmp/o.png", geometry_column="geom", value_field="trips",
    )
    params = fake_executor.calls[0][1]
    feats = params["features"]
    assert len(feats) == 4
    assert all(f["wkt"].startswith("POLYGON") for f in feats)
    # the geometry column must not also be sent as an attribute
    assert "geom" not in feats[0]
    assert set(feats[0]) == {"wkt", "zone_id", "trips"}


def test_lon_lat_columns_become_point_wkt(fake_executor, db):
    fake_executor.responses["render_wkt_features"] = _render_response("Point", 200)
    qgis_render_from_duckdb(
        db_path=db, query="SELECT trip_id, lon, lat, speed FROM pings",
        output_png="/tmp/o.png", lon_column="lon", lat_column="lat",
    )
    feats = fake_executor.calls[0][1]["features"]
    assert len(feats) == 200
    assert feats[0]["wkt"].startswith("POINT (")
    assert set(feats[0]) == {"wkt", "trip_id", "speed"}


def test_max_features_caps_rows_and_reports_it(fake_executor, db):
    fake_executor.responses["render_wkt_features"] = _render_response("Point", 50)
    result = qgis_render_from_duckdb(
        db_path=db, query="SELECT trip_id, lon, lat FROM pings",
        output_png="/tmp/o.png", lon_column="lon", lat_column="lat", max_features=50,
    )
    assert len(fake_executor.calls[0][1]["features"]) == 50
    assert result.row_limit_hit is True


def test_row_limit_not_flagged_when_under_the_cap(fake_executor, db):
    fake_executor.responses["render_wkt_features"] = _render_response()
    result = qgis_render_from_duckdb(
        db_path=db, query="SELECT zone_id, trips, geom FROM zones",
        output_png="/tmp/o.png", geometry_column="geom", max_features=1000,
    )
    assert result.row_limit_hit is False


def test_basemap_is_resolved_before_dispatch(fake_executor, db):
    fake_executor.responses["render_wkt_features"] = _render_response()
    qgis_render_from_duckdb(
        db_path=db, query="SELECT zone_id, trips, geom FROM zones",
        output_png="/tmp/o.png", geometry_column="geom", basemap="qms:opentopomap",
    )
    assert fake_executor.calls[0][1]["basemap_spec"] == {
        "kind": "qms", "id": "opentopomap", "opacity": 1.0,
    }


# ── the database is never modified ─────────────────────────────────────────


def test_connection_is_read_only(db):
    """The caller's database may be 8.8 GB of irreplaceable simulation output."""
    con = duckdb.connect(db, read_only=True)
    for sql in ("DROP TABLE zones", "DELETE FROM zones", "CREATE TABLE evil (x INT)"):
        with pytest.raises(Exception, match=r"read-only|Cannot execute"):
            con.execute(sql)
    con.close()
    assert duckdb.connect(db, read_only=True).execute(
        "SELECT count(*) FROM zones").fetchone()[0] == 4


def test_non_select_statement_is_rejected(fake_executor, db):
    fake_executor.responses["render_wkt_features"] = _render_response()
    with pytest.raises(QgisMcpWorkflowsError, match="query failed"):
        qgis_render_from_duckdb(
            db_path=db, query="DROP TABLE zones",
            output_png="/tmp/o.png", geometry_column="geom",
        )
    assert duckdb.connect(db, read_only=True).execute(
        "SELECT count(*) FROM zones").fetchone()[0] == 4


# ── errors name the fix ────────────────────────────────────────────────────


@pytest.mark.parametrize(("kwargs", "expected"), [
    ({"geometry_column": "geom", "lon_column": "lon", "lat_column": "lat"},
     "exactly one geometry source"),
    ({}, "exactly one geometry source"),
])
def test_geometry_source_must_be_unambiguous(fake_executor, db, kwargs, expected):
    with pytest.raises(QgisMcpWorkflowsError, match=expected):
        qgis_render_from_duckdb(
            db_path=db, query="SELECT * FROM zones", output_png="/tmp/o.png", **kwargs
        )


def test_missing_database_is_reported(fake_executor, tmp_path):
    with pytest.raises(QgisMcpWorkflowsError, match="not found"):
        qgis_render_from_duckdb(
            db_path=str(tmp_path / "nope.duckdb"), query="SELECT 1",
            output_png="/tmp/o.png", geometry_column="g",
        )


def test_missing_geometry_column_lists_what_is_available(fake_executor, db):
    with pytest.raises(QgisMcpWorkflowsError, match="zone_id"):
        qgis_render_from_duckdb(
            db_path=db, query="SELECT zone_id FROM zones",
            output_png="/tmp/o.png", geometry_column="geom",
        )


def test_empty_result_is_reported(fake_executor, db):
    with pytest.raises(QgisMcpWorkflowsError, match="no rows"):
        qgis_render_from_duckdb(
            db_path=db, query="SELECT * FROM zones WHERE 1=0",
            output_png="/tmp/o.png", geometry_column="geom",
        )


def test_all_errors_carry_a_next_step(fake_executor, db):
    """CLAUDE.md: every typed error ends with a suggested next call."""
    with pytest.raises(QgisMcpWorkflowsError) as exc:
        qgis_render_from_duckdb(
            db_path=db, query="SELECT * FROM zones WHERE 1=0",
            output_png="/tmp/o.png", geometry_column="geom",
        )
    assert "Next:" in str(exc.value)


# ── live render ────────────────────────────────────────────────────────────


@requires_headless
def test_renders_wkt_polygons(tmp_path, db):
    from qgis_mcp_workflows import executors
    from qgis_mcp_workflows.executors.headless import HeadlessExecutor

    executors.set_executor(HeadlessExecutor())
    out = tmp_path / "zones.png"
    result = qgis_render_from_duckdb(
        db_path=db, query="SELECT zone_id, trips, geom FROM zones",
        output_png=str(out), geometry_column="geom", value_field="trips",
        n_classes=4, mode="equal_interval", width=600, height=450, dpi=72,
    )
    assert out.exists() and out.stat().st_size > 1000
    assert result.geometry_type == "Polygon"
    assert result.n_features == 4
    assert result.breaks[0] == 25.0 and result.breaks[-1] == 275.0


@requires_headless
def test_renders_lon_lat_points(tmp_path, db):
    from qgis_mcp_workflows import executors
    from qgis_mcp_workflows.executors.headless import HeadlessExecutor

    executors.set_executor(HeadlessExecutor())
    out = tmp_path / "pings.png"
    result = qgis_render_from_duckdb(
        db_path=db, query="SELECT trip_id, lon, lat, speed FROM pings",
        output_png=str(out), lon_column="lon", lat_column="lat",
        value_field="speed", width=600, height=450, dpi=72,
    )
    assert out.exists() and out.stat().st_size > 1000
    assert result.geometry_type == "Point"
    assert result.n_features == 200


@requires_headless
def test_unparseable_geometry_names_the_cause(tmp_path, db):
    """A non-WKT column must not render an empty map and call it success."""
    from qgis_mcp_workflows import executors
    from qgis_mcp_workflows.executors.headless import HeadlessExecutor

    executors.set_executor(HeadlessExecutor())
    with pytest.raises(Exception, match="WKT"):
        qgis_render_from_duckdb(
            db_path=db, query="SELECT zone_id AS geom FROM zones",
            output_png=str(tmp_path / "x.png"), geometry_column="geom",
        )
