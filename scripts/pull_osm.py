"""Pull real Hyderabad OSM data -> a drive-graph GraphML + hospitals.csv.

Build-time tool. Run it with osmnx pulled in ad-hoc so osmnx does NOT become a
runtime dependency (the loaders only ever read the resulting GraphML with
networkx):

    uv run --with osmnx scripts/pull_osm.py
    uv run --with osmnx scripts/pull_osm.py --bbox 78.40 17.30 78.60 17.45 --out data/hyderabad --name hyderabad_drive

Defaults to the **Musi river corridor** through central Hyderabad (the flood
zone). Output:

    <out>/<name>.graphml   directed drive graph; nodes carry x/y, edges carry length
    <out>/hospitals.csv    hospital_id,lat,lon,data_label  (loaders snap lat/lon -> nearest node)

Hospitals are written in the *lat/lon* form so ``load_hospitals`` exercises its
real nearest-node resolution rather than us duplicating that logic here.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import networkx as nx
import osmnx as ox

# (left/west, bottom/south, right/east, top/north) in EPSG:4326 degrees.
# Musi river corridor: Old City -> Chaderghat -> Nampally -> Abids.
MUSI_CORRIDOR = (78.445, 17.335, 78.515, 17.395)


def pull_graph(bbox: tuple[float, float, float, float]) -> nx.MultiDiGraph:
    # retain_all=True keeps real disconnected pockets instead of silently
    # dropping everything outside the largest connected component.
    return ox.graph_from_bbox(bbox, network_type="drive", retain_all=True)


def pull_hospitals(bbox: tuple[float, float, float, float]):
    """Return [(hospital_id, lat, lon)] for every OSM hospital in the bbox.

    Handles both point hospitals and hospital *areas* (buildings drawn as
    polygons) by taking a representative point that is guaranteed to lie on
    the polygon.
    """
    features = ox.features_from_bbox(bbox, tags={"amenity": "hospital"})
    out = []
    for idx, row in features.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        point = geom if geom.geom_type == "Point" else geom.representative_point()
        # Index is (osm_type, osm_id); both live in separate id namespaces, so
        # keep both to stay unique and traceable back to OSM.
        osm_type, osm_id = idx if isinstance(idx, tuple) else ("element", idx)
        out.append((f"hosp_{osm_type}_{osm_id}", round(point.y, 7), round(point.x, 7)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bbox", nargs=4, type=float, default=MUSI_CORRIDOR, metavar=("L", "B", "R", "T"))
    ap.add_argument("--out", default="data/musi_corridor", help="output directory")
    ap.add_argument("--name", default="musi_corridor", help="graph basename (without .graphml)")
    args = ap.parse_args()

    bbox = tuple(args.bbox)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Pulling drive graph, bbox={bbox} ...")
    graph = pull_graph(bbox)
    graph_path = out_dir / f"{args.name}.graphml"
    ox.save_graphml(graph, graph_path)
    comps = sorted((len(c) for c in nx.weakly_connected_components(graph)), reverse=True)
    print(f"  {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges -> {graph_path}")
    print(f"  weakly-connected components: {len(comps)} (largest {comps[0]}, rest {comps[1:6]}...)")

    print("Pulling hospitals ...")
    hospitals = pull_hospitals(bbox)
    csv_path = out_dir / "hospitals.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hospital_id", "lat", "lon", "data_label"])
        for hid, lat, lon in hospitals:
            w.writerow([hid, lat, lon, "source_derived"])
    print(f"  {len(hospitals)} hospitals -> {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
