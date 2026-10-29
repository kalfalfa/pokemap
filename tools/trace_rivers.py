#!/usr/bin/env python3
"""
Trace the Bow River through Calgary along City of Calgary community boundaries.

Community boundaries follow the river centreline exactly, so the river is found as the
cheapest path through the community-boundary network, where cost grows with distance from a
rough guide line. Output replaces data_rivers.geojson in the project root.

Guides:
  upstream (west edge -> downtown): the "Bow at Calgary" river network from
    https://github.com/CH-Earth/meshflow  examples/bow-at-calgary-shapefiles/bcalgary_rivers.shp
    (MERIT-Hydro). Download that repo and pass the .shp path as the first argument.
  downstream (downtown -> south city limit): hand-placed points below (SOUTH_GUIDE).

  pip install shapely networkx pyshp
  python3 tools/trace_rivers.py /path/to/bcalgary_rivers.shp

The Elbow River was tried the same way but isn't reliably a community boundary, so it is
not traced.
"""
import csv, json, sys
from pathlib import Path
from shapely import wkt
from shapely.geometry import LineString, Point, shape, mapping
from shapely.ops import unary_union, linemerge
import networkx as nx
import shapefile

ROOT = Path(__file__).resolve().parents[1]
csv.field_size_limit(sys.maxsize)
K, M = 0.6293, 1 / 111_000
SOUTH_GUIDE = [(-114.032, 51.047), (-114.022, 51.046), (-114.014, 51.042), (-114.011, 51.034), (-114.013, 51.026),
               (-114.015, 51.02), (-114.02, 51.0), (-114.03, 50.985), (-114.025, 50.965), (-114.0, 50.94),
               (-113.99, 50.915), (-113.993, 50.9), (-113.985, 50.875), (-113.968, 50.852)]

def main(shp):
    comms = [wkt.loads(r["WKT"]) for r in csv.DictReader(open(ROOT / "data_calgary_communities.csv"))]
    edges = unary_union([c.boundary for c in comms])
    G = nx.Graph()
    key = lambda c: (round(c[0], 6), round(c[1], 6))
    for s in edges.geoms:
        cs = list(s.coords)
        for a, b in zip(cs, cs[1:]):
            G.add_edge(key(a), key(b), len=(((a[0] - b[0]) * K) ** 2 + (a[1] - b[1]) ** 2) ** 0.5)
    nodes = list(G.nodes)
    nearest = lambda pt: min(nodes, key=lambda n: ((n[0] - pt[0]) * K) ** 2 + (n[1] - pt[1]) ** 2)

    def follow(guide, tol_m):
        for u, v, d in G.edges(data=True):
            dist = guide.distance(Point((u[0] + v[0]) / 2, (u[1] + v[1]) / 2)) / M
            d["w"] = d["len"] * (1 + (dist / tol_m) ** 2)
        return LineString(nx.shortest_path(G, nearest(guide.coords[0]), nearest(guide.coords[-1]), weight="w"))

    west = unary_union([shape(s.__geo_interface__) for s in shapefile.Reader(shp).shapes()])
    west = west.intersection(unary_union(comms).buffer(0.001))
    if west.geom_type != "LineString":
        west = max(west.geoms, key=lambda g: g.length)
    bow_w = follow(west, 120)
    bow_s = follow(LineString([west.coords[0]] + SOUTH_GUIDE), 160)
    bow = linemerge(unary_union([bow_w, bow_s]))
    out = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {
        "name": "Bow River", "source": "traced along City of Calgary community boundaries; upstream guide from CH-Earth/meshflow (MERIT-Hydro)"},
        "geometry": mapping(bow)}]}
    (ROOT / "data_rivers.geojson").write_text(json.dumps(out))
    print("wrote data_rivers.geojson")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1])
