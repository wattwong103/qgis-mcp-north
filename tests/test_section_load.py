"""All-or-nothing assignment — no QGIS required (networkx)."""

from __future__ import annotations

from pathlib import Path

import pytest

networkx = pytest.importorskip("networkx")

FIXTURE_DIR = Path(__file__).parent / "fixtures"
NETWORK = FIXTURE_DIR / "tiny_network.geojson"
ZONES = FIXTURE_DIR / "tiny_zones.geojson"
OD = FIXTURE_DIR / "tiny_od.csv"


def test_assign_section_load_writes_volume_csv(tmp_path: Path):
    from qgis_mcp_workflows.server import qgis_assign_section_load

    out = tmp_path / "loads.csv"
    result = qgis_assign_section_load(
        od_csv=str(OD),
        network_path=str(NETWORK),
        output_csv=str(out),
        zones_path=str(ZONES),
    )
    assert out.exists()
    assert result.n_od == 6
    assert result.n_assigned >= 1
    assert result.n_links_with_load >= 1
    text = out.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "link_id,volume"
    assert "L_" in text


def test_assign_missing_od_column_raises(tmp_path: Path):
    from qgis_mcp_workflows.errors import FieldNotFoundError
    from qgis_mcp_workflows.server import qgis_assign_section_load

    bad = tmp_path / "bad.csv"
    bad.write_text("from,to,trip_count\nZ01,Z04,10\n", encoding="utf-8")
    with pytest.raises(FieldNotFoundError, match="origin"):
        qgis_assign_section_load(
            od_csv=str(bad),
            network_path=str(NETWORK),
            output_csv=str(tmp_path / "out.csv"),
            zones_path=str(ZONES),
        )


def test_aon_accumulates_on_shortest_path():
    from qgis_mcp_workflows.section_load import assign_aon, load_network

    graph, _node_xy = load_network(str(NETWORK))
    # Node keys are rounded endpoints of the four links.
    a = "139.690000,35.720000"
    d = "139.720000,35.700000"
    volumes, stats = assign_aon(
        graph,
        [{"origin": "o", "destination": "d", "trip_count": "10"}],
        origin_nodes={"o": a},
        dest_nodes={"d": d},
    )
    assert stats["n_assigned"] == 1
    assert sum(volumes.values()) >= 10
    assert set(volumes) <= {"L_AB", "L_AC", "L_BD", "L_CD"}
