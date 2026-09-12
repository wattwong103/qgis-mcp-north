"""Build GUFM zone GeoPackages from the Tokyo UKG shapefiles.

Replaces the PFLOW MFS leftover (DESIGN.md §8.5). GUFM's canonical polygons
are the 23 wards (JIS N03 / ``N03_007``) and Japan Standard Mesh L3 (1 km;
ADR-15). L4 KEY_CODE values in Tokyo23_mesh.shp are dissolved to 8-digit L3.

Run once (needs the ``drm`` extra for geopandas/pyogrio):

    uv run --no-sync --extra drm scripts/build_gufm_zones.py

Outputs (committed, small):
    assets/zones_tokyo23.gpkg   layer ``wards``, field ``zone_id`` = N03_007
    assets/zones_mesh_l3.gpkg   layer ``mesh_l3``, field ``zone_id`` = 8-digit mesh
    assets/tokyo23_home_counts.csv  ward counts from gufm personas sample (demo CSV)
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_GUFM = Path.home() / "Dropbox" / "gufm"
POLYGON = Path("10_data") / "urban_kg" / "urban_data" / "polygon"
PERSONAS_FULL = Path("10_data") / "pflow_personas.csv"
PERSONAS_SAMPLE = Path("10_data") / "pflow_personas_sample.csv"


def _require_geopandas():
    try:
        import geopandas as gpd  # noqa: F401
    except ImportError as err:
        raise SystemExit(
            "geopandas is required. Install with: uv sync --extra drm"
        ) from err
    return __import__("geopandas")


def build_wards(gufm: Path, output: Path) -> int:
    gpd = _require_geopandas()
    src = gufm / POLYGON / "Tokyo23.shp"
    if not src.exists():
        raise SystemExit(f"Tokyo23.shp not found: {src}")
    gdf = gpd.read_file(src, engine="pyogrio")
    gdf["zone_id"] = gdf["N03_007"].astype(str)
    gdf["name"] = gdf["N03_004"].astype(str)
    gdf["pref"] = gdf["N03_001"].astype(str)
    out = gdf[["zone_id", "name", "pref", "geometry"]].copy()
    out = out.set_crs(4326, allow_override=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_file(output, layer="wards", driver="GPKG")
    return len(out)


def build_mesh_l3(gufm: Path, output: Path) -> int:
    gpd = _require_geopandas()
    src = gufm / POLYGON / "Tokyo23_mesh.shp"
    if not src.exists():
        raise SystemExit(f"Tokyo23_mesh.shp not found: {src}")
    gdf = gpd.read_file(src, engine="pyogrio")
    gdf["zone_id"] = gdf["KEY_CODE"].astype(str).str[:8]
    dissolved = gdf.dissolve(by="zone_id", as_index=False)
    out = dissolved[["zone_id", "geometry"]].copy()
    if out.crs is None:
        out = out.set_crs(4326, allow_override=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_file(output, layer="mesh_l3", driver="GPKG")
    return len(out)


def build_home_counts(gufm: Path, output: Path) -> int:
    src = gufm / PERSONAS_FULL
    if not src.exists():
        src = gufm / PERSONAS_SAMPLE
    if not src.exists():
        raise SystemExit(f"personas CSV not found under {gufm / '10_data'}")
    counts: Counter[str] = Counter()
    with src.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            zone = str(row.get("home_zone") or "").strip()
            if zone:
                counts[zone] += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["zone_id", "n_persons"])
        for zone, n in sorted(counts.items()):
            w.writerow([zone, n])
    return len(counts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gufm-root", type=Path, default=DEFAULT_GUFM)
    parser.add_argument("--out-dir", type=Path, default=REPO / "assets")
    args = parser.parse_args()
    wards_path = args.out_dir / "zones_tokyo23.gpkg"
    n_wards = build_wards(args.gufm_root, wards_path)
    n_mesh = build_mesh_l3(args.gufm_root, args.out_dir / "zones_mesh_l3.gpkg")
    n_csv = build_home_counts(args.gufm_root, args.out_dir / "tokyo23_home_counts.csv")
    keep = {str(z) for z in range(13101, 13124)}
    filtered = []
    csv_path = args.out_dir / "tokyo23_home_counts.csv"
    with csv_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        if row["zone_id"] in keep:
            filtered.append(row)
    if filtered:
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["zone_id", "n_persons"])
            w.writeheader()
            w.writerows(filtered)
        n_csv = len(filtered)
    print(f"wards={n_wards} mesh_l3={n_mesh} home_zones={n_csv} -> {args.out_dir}")


if __name__ == "__main__":
    main()
