"""Live GUFM figure smoke: 23-ward choropleth + routed trajectories + pptx.

Requires QGIS Desktop with QGIS MCP Workflows started on :9877
(Stop/Start after a plugin code change).

    uv run --no-sync --extra pptx scripts/demo_gufm.py
"""

from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = Path("/tmp/gufm-live")
_GUFM = Path.home() / "Dropbox" / "gufm"
TRAJ = _GUFM / "21_decks/2026-06-12_lab_meeting/_mapdata/gt_routed.csv"
HERO_STOPS = _GUFM / "21_decks/2026-06-12_lab_meeting/_mapdata/hero_stops.csv"
DRM_TSV = _GUFM / "10_data/network_cache/drm_inner_tokyo.tsv"
RAIL_TSV = _GUFM / "10_data/network_cache/rail_inner_tokyo.tsv"
WARDS = _GUFM / "10_data/urban_kg/urban_data/polygon/Tokyo23.shp"
RAIL = _GUFM / "10_data/urban_kg/urban_data/road/Tokyo_railway.shp"


def main() -> None:
    from qgis_mcp_workflows.executors import set_executor
    from qgis_mcp_workflows.executors.plugin import PluginExecutor
    from qgis_mcp_workflows.server import (
        qgis_figures_to_pptx,
        qgis_ping,
        qgis_render_choropleth,
        qgis_render_trajectory,
        qgis_route_on_network,
    )

    transport = os.environ.get("QGIS_MCP_WORKFLOWS_TRANSPORT", "plugin")
    set_executor(PluginExecutor())
    OUT.mkdir(parents=True, exist_ok=True)
    print("transport", transport, qgis_ping())

    ch = qgis_render_choropleth(
        zones_path=str(REPO / "assets" / "zones_tokyo23.gpkg"),
        value_csv=str(REPO / "assets" / "tokyo23_home_counts.csv"),
        value_field="n_persons",
        join_field="zone_id",
        output_png=str(OUT / "gufm_wards.png"),
        palette="gufm",
        label_field="name",
        title="GUFM home-zone persons (23 wards)",
        legend=True,
        scale_bar=True,
        north_arrow=True,
        basemap="light",
    )
    print("choropleth", ch.join, ch.output_path)

    tr_kwargs = dict(
        input_path=str(TRAJ),
        output_png=str(OUT / "gufm_traj.png"),
        mode_col="mode",
        render_mode="lines",
        scale_bar=True,
        north_arrow=True,
        basemap="light",
    )
    underlays = [p for p in (WARDS, RAIL) if p.exists()]
    if underlays:
        tr_kwargs["basemap_paths"] = [str(p) for p in underlays]
    tr = qgis_render_trajectory(**tr_kwargs)
    print("trajectory", tr.n_trajectories, tr.modes, tr.output_path)

    figures = [ch.output_path, tr.output_path]
    captions = [
        "23-ward home-zone persons\njoin_field=zone_id (N03_007)",
        "Routed GT trajectories\nmode_col=mode",
    ]
    if HERO_STOPS.exists() and DRM_TSV.exists():
        routed = qgis_route_on_network(
            input_csv=str(HERO_STOPS),
            network_path=str(DRM_TSV),
            rail_network_path=str(RAIL_TSV) if RAIL_TSV.exists() else None,
            output_csv=str(OUT / "hero_routed.csv"),
            time_col="clock",
        )
        print("route", routed.n_routed, "of", routed.n_legs, "legs")
        hero = qgis_render_trajectory(
            input_path=routed.output_csv,
            output_png=str(OUT / "gufm_hero_routed.png"),
            mode_col="mode",
            render_mode="lines",
            scale_bar=True,
            north_arrow=True,
            basemap_paths=[str(p) for p in (WARDS, RAIL) if p.exists()] or None,
        )
        figures.append(hero.output_path)
        captions.append("DRM-routed hero_stops\nqgis_route_on_network")

    deck = qgis_figures_to_pptx(
        figure_paths=figures,
        pptx_path=str(OUT / "gufm_figures.pptx"),
        layout="title_image_caption",
        captions=captions,
    )
    print("pptx", deck.pptx_path, "slides", deck.n_slides_added)


if __name__ == "__main__":
    main()
