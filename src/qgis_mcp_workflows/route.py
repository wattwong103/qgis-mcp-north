"""Snap-and-route point sequences onto a line network (GUFM DRM / rail).

MCP-side, no PyQGIS. GUFM's matplotlib router lives in
``gufm/scripts/figures/routing.py`` and reads
``10_data/network_cache/{drm,rail}_inner_tokyo.tsv``. This module is the
workflow equivalent: same TSV schema, plus GeoJSON/GeoPackage via
``section_load.load_network``.

Requires the ``[network]`` extra (networkx). scipy is optional (faster snap).
"""

from __future__ import annotations

import csv
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from qgis_mcp_workflows.section_load import _require_networkx, load_network, snap_to_nodes

# Tokyo-ish cosine for KD-tree scaling (same constant GUFM routing.py uses).
_COSLAT = math.cos(math.radians(35.7))
_KM = 111.0
_CACHE: dict[tuple[str, int, int], Any] = {}


def _parse_wkt_linestring(wkt: str) -> list[tuple[float, float]]:
    inner = wkt[wkt.find("(") + 1 : wkt.rfind(")")]
    coords: list[tuple[float, float]] = []
    for part in inner.split(","):
        bits = part.split()
        if len(bits) < 2:
            continue
        coords.append((float(bits[0]), float(bits[1])))
    return coords


def load_tsv_network(path: str) -> tuple[Any, dict[str, tuple[float, float]]]:
    """Load GUFM ``drm_inner_tokyo.tsv`` / ``rail_inner_tokyo.tsv``.

    Columns: nodeA, nodeB, fromLon, fromLat, toLon, toLat, [roadclass], WKT.
    Undirected (matches GUFM ``routing.build_net``).
    """
    nx = _require_networkx()
    graph = nx.Graph()
    node_xy: dict[str, tuple[float, float]] = {}
    n_rows = 0
    with Path(path).open(encoding="utf-8") as fh:
        for raw in fh:
            n_rows += 1
            cols = raw.rstrip("\n").rstrip("\r").split("\t")
            if len(cols) < 7:
                continue
            wkt = cols[-1]
            if "LINESTRING" not in wkt.upper():
                continue
            a, b = str(cols[0]), str(cols[1])
            try:
                flo, fla = float(cols[2]), float(cols[3])
                tlo, tla = float(cols[4]), float(cols[5])
            except ValueError:
                continue
            coords = _parse_wkt_linestring(wkt)
            if len(coords) < 2:
                coords = [(flo, fla), (tlo, tla)]
            node_xy.setdefault(a, (flo, fla))
            node_xy.setdefault(b, (tlo, tla))
            length = 0.0
            for (x1, y1), (x2, y2) in pairwise(coords):
                length += math.hypot((x2 - x1) * _COSLAT, y2 - y1) * _KM * 1000.0
            if graph.has_edge(a, b):
                continue
            graph.add_edge(
                a,
                b,
                link_id=f"{a}_{b}",
                length=length or 1.0,
                coords=coords,
            )
    if graph.number_of_edges() == 0:
        raise ValueError(f"{path}: no LINESTRING rows ({n_rows} lines)")
    return graph, node_xy


def load_route_network(
    path: str,
    link_id_field: str = "link_id",
    from_node_field: str | None = None,
    to_node_field: str | None = None,
) -> tuple[Any, dict[str, tuple[float, float]]]:
    """Load a routable undirected graph. TSV → GUFM cache; else GeoJSON/GPKG."""
    p = Path(path)
    key = (str(p.resolve()), int(p.stat().st_mtime), int(p.stat().st_size))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    suffix = p.suffix.lower()
    if suffix in {".tsv", ".txt"}:
        graph, node_xy = load_tsv_network(str(p))
    else:
        directed, node_xy = load_network(
            str(p),
            link_id_field=link_id_field,
            from_node_field=from_node_field,
            to_node_field=to_node_field,
        )
        nx = _require_networkx()
        graph = nx.Graph()
        for u, v, data in directed.edges(data=True):
            if graph.has_edge(u, v):
                continue
            coords = [node_xy[u], node_xy[v]]
            graph.add_edge(
                u,
                v,
                link_id=data.get("link_id", f"{u}_{v}"),
                length=data.get("length", 1.0),
                coords=coords,
            )
    _CACHE.clear()
    _CACHE[key] = (graph, node_xy)
    return graph, node_xy


def _snap_one(
    lon: float,
    lat: float,
    node_xy: dict[str, tuple[float, float]],
    max_snap_km: float,
) -> str | None:
    mapping = snap_to_nodes({"p": (lon, lat)}, node_xy)
    nid = mapping.get("p")
    if nid is None:
        return None
    x, y = node_xy[nid]
    dist_km = math.hypot((x - lon) * _COSLAT, y - lat) * _KM
    if dist_km > max_snap_km:
        return None
    return nid


def _edge_coords(graph: Any, u: str, v: str, node_xy: dict[str, tuple[float, float]]) -> list[tuple[float, float]]:
    data = graph[u][v]
    coords = list(data.get("coords") or [node_xy[u], node_xy[v]])
    if len(coords) < 2:
        return [node_xy[u], node_xy[v]]
    ux, uy = node_xy[u]
    if math.hypot(coords[0][0] - ux, coords[0][1] - uy) > math.hypot(
        coords[-1][0] - ux, coords[-1][1] - uy
    ):
        coords = list(reversed(coords))
    return coords


def route_leg(
    graph: Any,
    node_xy: dict[str, tuple[float, float]],
    src: tuple[float, float],
    dst: tuple[float, float],
    max_snap_km: float = 3.0,
) -> tuple[list[tuple[float, float]], list[str], str]:
    """Return (polyline, link_ids per vertex, status).

    status is ``routed`` or ``straight``.
    """
    nx = _require_networkx()
    s = _snap_one(src[0], src[1], node_xy, max_snap_km)
    t = _snap_one(dst[0], dst[1], node_xy, max_snap_km)
    if s is None or t is None or s == t:
        return [src, dst], ["", ""], "straight"
    try:
        nodes = nx.shortest_path(graph, s, t, weight="length")
    except Exception:
        return [src, dst], ["", ""], "straight"
    pts: list[tuple[float, float]] = [src]
    link_ids: list[str] = [""]
    for u, v in pairwise(nodes):
        coords = _edge_coords(graph, u, v, node_xy)
        lid = str(graph[u][v].get("link_id") or f"{u}_{v}")
        for c in coords[1:]:
            pts.append(c)
            link_ids.append(lid)
    if pts[-1] != dst:
        pts.append(dst)
        link_ids.append(link_ids[-1] if link_ids else "")
    return pts, link_ids, "routed"


def _is_rail(mode: str, rail_modes: set[str]) -> bool:
    return str(mode).upper() in rail_modes


def route_sequences(
    rows: list[dict[str, str]],
    road: tuple[Any, dict[str, tuple[float, float]]],
    *,
    rail: tuple[Any, dict[str, tuple[float, float]]] | None = None,
    id_col: str = "trip_id",
    seq_col: str = "seq",
    lon_col: str = "lon",
    lat_col: str = "lat",
    mode_col: str | None = "mode",
    time_col: str | None = None,
    default_mode: str = "CAR",
    rail_modes: set[str] | None = None,
    max_snap_km: float = 3.0,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Route consecutive stops per trip_id. Missing id_col → one trip."""
    rail_modes = rail_modes or {"RAIL", "TRAIN"}
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    order: list[str] = []
    for row in rows:
        tid = str(row.get(id_col) or "t0")
        if tid not in grouped:
            order.append(tid)
        grouped[tid].append(row)
    for tid in order:
        grouped[tid].sort(key=lambda r: _seq_key(r.get(seq_col, "0")))

    out: list[dict[str, str]] = []
    stats = {"n_trips": 0, "n_legs": 0, "n_routed": 0, "n_straight": 0, "n_points": 0}
    base = datetime(2026, 4, 24, 8, 0, 0)
    seq_out = 0
    for tid in order:
        stops = grouped[tid]
        stats["n_trips"] += 1
        if len(stops) < 2:
            continue
        for a, b in pairwise(stops):
            stats["n_legs"] += 1
            mode = (b.get(mode_col) if mode_col else None) or a.get(mode_col) or default_mode
            use_rail = rail is not None and _is_rail(mode, rail_modes)
            net, nxy = rail if use_rail else road
            src = (float(a[lon_col]), float(a[lat_col]))
            dst = (float(b[lon_col]), float(b[lat_col]))
            pts, lids, status = route_leg(net, nxy, src, dst, max_snap_km=max_snap_km)
            if status == "routed":
                stats["n_routed"] += 1
            else:
                stats["n_straight"] += 1
            t0 = _row_time(a, time_col, base)
            for i, ((lon, lat), lid) in enumerate(zip(pts, lids, strict=False)):
                ts = t0 + timedelta(seconds=i * 30)
                out.append(
                    {
                        "trip_id": f"{tid}_{stats['n_legs'] - 1}",
                        "seq": str(seq_out),
                        "lon": f"{lon:.6f}",
                        "lat": f"{lat:.6f}",
                        "mode": str(mode),
                        "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "link_id": lid,
                    }
                )
                seq_out += 1
                stats["n_points"] += 1
    return out, stats


def _seq_key(raw: str) -> tuple[int, str]:
    try:
        return (int(float(raw)), str(raw))
    except (TypeError, ValueError):
        return (0, str(raw))


def _row_time(row: dict[str, str], time_col: str | None, base: datetime) -> datetime:
    if not time_col:
        return base
    raw = (row.get(time_col) or "").strip()
    if not raw:
        return base
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(raw, fmt)
            if fmt in ("%H:%M:%S", "%H:%M"):
                return base.replace(hour=parsed.hour, minute=parsed.minute, second=parsed.second)
            return parsed
        except ValueError:
            continue
    return base


def read_stop_csv(path: str) -> tuple[list[dict[str, str]], list[str]]:
    with Path(path).open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = list(reader.fieldnames or [])
        rows = [{k: (v or "") for k, v in row.items()} for row in reader]
    return rows, cols


def write_routed_csv(path: str, rows: list[dict[str, str]]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    fields = ["trip_id", "seq", "lon", "lat", "mode", "datetime", "link_id"]
    with Path(path).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
