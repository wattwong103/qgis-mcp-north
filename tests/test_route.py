"""qgis_route_on_network — networkx, no QGIS."""

from __future__ import annotations

from pathlib import Path

import pytest

networkx = pytest.importorskip("networkx")

FIXTURE_DIR = Path(__file__).parent / "fixtures"
NETWORK = FIXTURE_DIR / "tiny_network.geojson"


def _stops_csv(path: Path) -> Path:
    path.write_text(
        "trip_id,seq,lon,lat,mode\n"
        "t1,0,139.690,35.720,CAR\n"
        "t1,1,139.720,35.700,CAR\n",
        encoding="utf-8",
    )
    return path


def test_route_on_tiny_network_writes_polyline(tmp_path: Path):
    from qgis_mcp_workflows.server import qgis_route_on_network

    out = tmp_path / "routed.csv"
    result = qgis_route_on_network(
        input_csv=str(_stops_csv(tmp_path / "stops.csv")),
        network_path=str(NETWORK),
        output_csv=str(out),
    )
    assert out.exists()
    assert result.n_trips == 1
    assert result.n_legs == 1
    assert result.n_routed == 1
    assert result.n_straight == 0
    assert result.n_points >= 2
    text = out.read_text(encoding="utf-8")
    header = text.splitlines()[0]
    assert header == "trip_id,seq,lon,lat,mode,datetime,link_id"
    assert "CAR" in text


def test_route_tsv_cache_schema(tmp_path: Path):
    from qgis_mcp_workflows.server import qgis_route_on_network

    tsv = tmp_path / "drm.tsv"
    tsv.write_text(
        "A\tB\t139.69\t35.72\t139.72\t35.72\t1\tLINESTRING(139.69 35.72,139.72 35.72)\n"
        "B\tD\t139.72\t35.72\t139.72\t35.70\t1\tLINESTRING(139.72 35.72,139.72 35.70)\n"
        "A\tC\t139.69\t35.72\t139.69\t35.70\t1\tLINESTRING(139.69 35.72,139.69 35.70)\n"
        "C\tD\t139.69\t35.70\t139.72\t35.70\t1\tLINESTRING(139.69 35.70,139.72 35.70)\n",
        encoding="utf-8",
    )
    out = tmp_path / "routed.csv"
    result = qgis_route_on_network(
        input_csv=str(_stops_csv(tmp_path / "stops.csv")),
        network_path=str(tsv),
        output_csv=str(out),
    )
    assert result.n_routed == 1
    body = out.read_text(encoding="utf-8")
    assert "A_B" in body or "B_D" in body or "A_C" in body


def test_straight_fallback_when_far(tmp_path: Path):
    from qgis_mcp_workflows.server import qgis_route_on_network

    far = tmp_path / "far.csv"
    far.write_text(
        "trip_id,seq,lon,lat,mode\n"
        "t1,0,0.0,0.0,CAR\n"
        "t1,1,1.0,1.0,CAR\n",
        encoding="utf-8",
    )
    result = qgis_route_on_network(
        input_csv=str(far),
        network_path=str(NETWORK),
        output_csv=str(tmp_path / "out.csv"),
        max_snap_km=3.0,
    )
    assert result.n_straight == 1
    assert result.n_routed == 0


def test_missing_lon_raises(tmp_path: Path):
    from qgis_mcp_workflows.errors import FieldNotFoundError
    from qgis_mcp_workflows.server import qgis_route_on_network

    bad = tmp_path / "bad.csv"
    bad.write_text("trip_id,seq,x,y\nt1,0,1,2\n", encoding="utf-8")
    with pytest.raises(FieldNotFoundError, match="lon"):
        qgis_route_on_network(
            input_csv=str(bad),
            network_path=str(NETWORK),
            output_csv=str(tmp_path / "out.csv"),
        )
