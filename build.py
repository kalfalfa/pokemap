#!/usr/bin/env python3
"""
Build areas.geojson and index.html for Scan Zones.

Selectable areas:
  Calgary   one zone per City of Calgary community; communities under MIN_KM2 (neighbourhoods.py)
            are grouped with a neighbour so no zone is smaller than that.
  Airdrie   one zone. Source: data_airdrie_boundary.geojson (City of Airdrie open data,
            municipal boundary, GeoJSON). Falls back to fallback_airdrie_boundary_2011.geojson.
  CrossIron one zone around the mall and Century Downs.

Context (not selectable)
  data_calgary_communities.csv     City of Calgary community polygons + names (the Calgary areas)
  data_rivers.geojson              the Bow River; grouping avoids joining areas across it
  data_calgary_speed_limits.csv    major roads, drawn only when map tiles can't load
  carto_key.txt (or env CARTO_KEY) CARTO Basemaps API key, https://carto.com/basemaps/apikey/

Requires: pip install shapely   (Python 3.8+; shapely 2.x)
Run:      python3 build.py      -> writes areas.geojson and index.html
"""
import csv, json, os, re, sys
from math import cos, radians
from pathlib import Path
from shapely import wkt
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union, polylabel

HERE = Path(__file__).parent
csv.field_size_limit(sys.maxsize)
TOL, ROUND = 0.00012, 5

def first_existing(*names):
    for n in names:
        if (HERE / n).exists():
            return HERE / n
    sys.exit("missing input: " + " or ".join(names))

def rnd_geom(g):
    def r(c):
        return [r(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], ROUND), round(c[1], ROUND)]
    m = mapping(g); return {"type": m["type"], "coordinates": r(m["coordinates"])}

def km2(g):
    c = g.centroid; return g.area * 111.32 * cos(radians(c.y)) * 110.57

def label_point(g):
    big = max(g.geoms, key=lambda p: p.area) if g.geom_type == "MultiPolygon" else g
    p = polylabel(big, tolerance=0.0002); return [round(p.y, 5), round(p.x, 5)]

def valid(g): return g if g.is_valid else g.buffer(0)

def load_polys(path):
    return [valid(shape(f["geometry"])) for f in json.load(open(path, encoding="utf-8"))["features"] if f.get("geometry")]

# ---- Calgary zones: neighbourhoods, small ones grouped -------------------
import neighbourhoods as NB
groups = NB.group(NB.MIN_KM2)
calgary = unary_union([g["g"] for g in groups])
groups = [g for g in groups if not g["undeveloped"]]
features = []
for g in sorted(groups, key=lambda g: NB.label(g)):
    fid = "C-" + "-".join(sorted(g["codes"]))[:60]
    names = list(dict.fromkeys(g["names"]))
    features.append({"type": "Feature", "id": fid, "properties": {
        "id": fid, "name": NB.label(g), "short": NB.label(g), "region": "Calgary",
        "places": names, "km2": round(km2(g["g"]), 1), "lp": label_point(g["g"])},
        "geometry": rnd_geom(g["g"].simplify(TOL, preserve_topology=True))})
print(f"Calgary: {len(features)} zones (neighbourhoods under {NB.MIN_KM2} km2 grouped)")

# ---- Airdrie -------------------------------------------------------------
air_file = first_existing("data_airdrie_boundary.geojson", "fallback_airdrie_boundary_2011.geojson")
air = unary_union(load_polys(air_file))
print(f"Airdrie from {air_file.name}")
features.append({"type": "Feature", "id": "AIRDRIE", "properties": {
    "id": "AIRDRIE", "name": "Airdrie", "short": "Airdrie", "region": "Airdrie", "places": ["Airdrie"],
    "km2": round(km2(air), 1), "lp": label_point(air)}, "geometry": rnd_geom(air.simplify(TOL, preserve_topology=True))})

# ---- CrossIron -----------------------------------------------------------
cim = box(-114.0045, 51.1955, -113.9745, 51.2120).difference(calgary.buffer(0))
cim = max(cim.geoms, key=lambda p: p.area) if cim.geom_type == "MultiPolygon" else cim
features.append({"type": "Feature", "id": "CROSSIRON", "properties": {
    "id": "CROSSIRON", "name": "CrossIron Mills", "short": "CrossIron", "region": "CrossIron",
    "places": ["CrossIron Mills", "Century Downs", "Balzac"], "km2": round(km2(cim), 1), "lp": label_point(cim)},
    "geometry": rnd_geom(cim.simplify(TOL))})

ids = [f["id"] for f in features]; assert len(ids) == len(set(ids))
areas = {"type": "FeatureCollection", "features": features}
(HERE / "areas.geojson").write_text(json.dumps(areas, separators=(",", ":")), encoding="utf-8")

key = os.environ.get("CARTO_KEY", "")
if not key and (HERE / "carto_key.txt").exists():
    key = ((HERE / "carto_key.txt").read_text().strip().splitlines() or [""])[0].strip()
if not key:
    print("warning: no CARTO key (set CARTO_KEY or create carto_key.txt); tiles will show 'API key required'")

html = (HERE / "app.template.html").read_text(encoding="utf-8")
html = html.replace("/*__AREAS__*/null", json.dumps(areas, separators=(",", ":")))
html = html.replace('/*__CARTO_KEY__*/""', json.dumps(key))
(HERE / "index.html").write_text(html, encoding="utf-8")
print(f"index.html {len(html)//1024} KB")
