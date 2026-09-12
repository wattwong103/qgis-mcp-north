"""All-or-nothing traffic assignment — MCP-side, no PyQGIS.

Builds a directed graph from a line network (GeoJSON or GeoPackage), snaps OD
zone centroids (or already-numeric node ids) to nearest graph nodes, then
assigns each OD volume onto the shortest path. Output is ``{link_id: volume}``.

Requires the ``[network]`` extra (networkx). scipy is optional (faster snap).
"""

from __future__ import annotations

import csv
import json
import math
from itertools import pairwise
from pathlib import Path
from typing import Any


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _node_key(lon: float, lat: float, ndigits: int = 6) -> str:
    return f"{lon:.{ndigits}f},{lat:.{ndigits}f}"


def _linestring_length_m(coords: list[list[float]]) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in pairwise(coords):
        total += _haversine_m(x1, y1, x2, y2)
    return total or 1.0


def _polygon_centroid(coords: list[list[float]]) -> tuple[float, float]:
    ring = coords[:-1] if coords[0] == coords[-1] else coords
    if not ring:
        return 0.0, 0.0
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


def _require_networkx():
    try:
        import networkx as nx
    except ImportError as exc:
        from qgis_mcp_workflows.errors import NetworkExtraMissingError

        raise NetworkExtraMissingError() from exc
    return nx


def load_network(
    path: str,
    link_id_field: str = "link_id",
    from_node_field: str | None = None,
    to_node_field: str | None = None,
) -> tuple[Any, dict[str, tuple[float, float]]]:
    """Return (DiGraph with edge attr link_id/length, {node: (lon, lat)})."""
    nx = _require_networkx()
    features = _read_line_features(path)
    graph = nx.DiGraph()
    node_xy: dict[str, tuple[float, float]] = {}
    for feat in features:
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") == "MultiLineString":
            coords = coords[0] if coords else []
        if len(coords) < 2:
            continue
        link_id = str(props.get(link_id_field, ""))
        if not link_id:
            continue
        if from_node_field and to_node_field and props.get(from_node_field) and props.get(to_node_field):
            u, v = str(props[from_node_field]), str(props[to_node_field])
            node_xy.setdefault(u, (float(coords[0][0]), float(coords[0][1])))
            node_xy.setdefault(v, (float(coords[-1][0]), float(coords[-1][1])))
        else:
            u = _node_key(float(coords[0][0]), float(coords[0][1]))
            v = _node_key(float(coords[-1][0]), float(coords[-1][1]))
            node_xy[u] = (float(coords[0][0]), float(coords[0][1]))
            node_xy[v] = (float(coords[-1][0]), float(coords[-1][1]))
        graph.add_edge(u, v, link_id=link_id, length=_linestring_length_m(coords))
    return graph, node_xy


def _read_line_features(path: str) -> list[dict]:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in {".geojson", ".json"}:
        data = json.loads(p.read_text(encoding="utf-8"))
        return list(data.get("features") or [])
    try:
        import pyogrio
    except ImportError as exc:
        from qgis_mcp_workflows.errors import NetworkExtraMissingError

        raise NetworkExtraMissingError(
            extra_hint="GeoPackage networks also need pyogrio (the drm extra)."
        ) from exc
    table = pyogrio.read_dataframe(path)
    features: list[dict] = []
    for _, row in table.iterrows():
        geom = row.geometry
        if geom is None:
            continue
        coords = list(geom.coords) if geom.geom_type == "LineString" else []
        if geom.geom_type == "MultiLineString" and len(geom.geoms):
            coords = list(geom.geoms[0].coords)
        props = {k: row[k] for k in table.columns if k != "geometry"}
        features.append(
            {
                "properties": {k: (None if _is_na(v) else v) for k, v in props.items()},
                "geometry": {"type": "LineString", "coordinates": [list(c) for c in coords]},
            }
        )
    return features


def _is_na(v: object) -> bool:
    try:
        return v != v  # NaN
    except Exception:
        return False


def load_zone_centroids(path: str, zone_id_field: str = "zone_id") -> dict[str, tuple[float, float]]:
    """zone_id → (lon, lat) from a polygon/point GeoJSON (or gpkg via pyogrio)."""
    p = Path(path)
    if p.suffix.lower() in {".geojson", ".json"}:
        data = json.loads(p.read_text(encoding="utf-8"))
        out: dict[str, tuple[float, float]] = {}
        for feat in data.get("features") or []:
            props = feat.get("properties") or {}
            zid = props.get(zone_id_field)
            if zid is None:
                continue
            geom = feat.get("geometry") or {}
            gtype = geom.get("type")
            coords = geom.get("coordinates") or []
            if gtype == "Point":
                out[str(zid)] = (float(coords[0]), float(coords[1]))
            elif gtype == "Polygon" and coords:
                out[str(zid)] = _polygon_centroid(coords[0])
            elif gtype == "MultiPolygon" and coords and coords[0]:
                out[str(zid)] = _polygon_centroid(coords[0][0])
        return out
    try:
        import pyogrio
    except ImportError as exc:
        from qgis_mcp_workflows.errors import NetworkExtraMissingError

        raise NetworkExtraMissingError(
            extra_hint="Non-GeoJSON zone layers need pyogrio (the drm extra)."
        ) from exc
    table = pyogrio.read_dataframe(path)
    out = {}
    for _, row in table.iterrows():
        zid = row.get(zone_id_field)
        if zid is None:
            continue
        geom = row.geometry
        if geom is None:
            continue
        c = geom.centroid
        out[str(zid)] = (float(c.x), float(c.y))
    return out


def snap_to_nodes(
    points: dict[str, tuple[float, float]],
    node_xy: dict[str, tuple[float, float]],
) -> dict[str, str]:
    """Map each id to the nearest graph node key (Euclidean on lon/lat)."""
    if not node_xy:
        return {}
    nodes = list(node_xy.items())
    coords = [(xy[0], xy[1]) for _, xy in nodes]
    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(coords)
        mapping = {}
        for zid, (lon, lat) in points.items():
            _, idx = tree.query([lon, lat])
            mapping[zid] = nodes[int(idx)][0]
        return mapping
    except ImportError:
        mapping = {}
        for zid, (lon, lat) in points.items():
            best, best_d = nodes[0][0], float("inf")
            for nid, (x, y) in nodes:
                d = (x - lon) ** 2 + (y - lat) ** 2
                if d < best_d:
                    best, best_d = nid, d
            mapping[zid] = best
        return mapping


def assign_aon(
    graph: Any,
    od_rows: list[dict[str, Any]],
    origin_nodes: dict[str, str],
    dest_nodes: dict[str, str],
    origin_col: str = "origin",
    dest_col: str = "destination",
    value_col: str = "trip_count",
) -> tuple[dict[str, float], dict[str, Any]]:
    """All-or-nothing assignment. Returns (volumes, stats)."""
    nx = _require_networkx()
    volumes: dict[str, float] = {}
    n_od = 0
    n_assigned = 0
    n_unassigned = 0
    unmatched_o = 0
    unmatched_d = 0
    for row in od_rows:
        n_od += 1
        o_key = str(row[origin_col])
        d_key = str(row[dest_col])
        try:
            value = float(row[value_col])
        except (TypeError, ValueError):
            n_unassigned += 1
            continue
        o_node = origin_nodes.get(o_key)
        d_node = dest_nodes.get(d_key)
        if o_node is None:
            unmatched_o += 1
            n_unassigned += 1
            continue
        if d_node is None:
            unmatched_d += 1
            n_unassigned += 1
            continue
        if o_node == d_node:
            n_unassigned += 1
            continue
        try:
            path = nx.shortest_path(graph, o_node, d_node, weight="length")
        except nx.NetworkXNoPath:
            n_unassigned += 1
            continue
        n_assigned += 1
        for u, v in pairwise(path):
            link_id = graph[u][v]["link_id"]
            volumes[link_id] = volumes.get(link_id, 0.0) + value
    stats = {
        "n_od": n_od,
        "n_assigned": n_assigned,
        "n_unassigned": n_unassigned,
        "n_unmatched_origins": unmatched_o,
        "n_unmatched_destinations": unmatched_d,
        "n_links_with_load": len(volumes),
        "n_nodes": graph.number_of_nodes(),
        "n_edges": graph.number_of_edges(),
    }
    return volumes, stats


def write_volume_csv(path: str, volumes: dict[str, float], link_id_col: str = "link_id") -> str:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([link_id_col, "volume"])
        for link_id, vol in sorted(volumes.items(), key=lambda kv: -kv[1]):
            w.writerow([link_id, vol])
    return str(out.resolve())


def read_od_csv(path: str) -> tuple[list[dict[str, str]], list[str]]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        cols = list(reader.fieldnames or [])
        return list(reader), cols
