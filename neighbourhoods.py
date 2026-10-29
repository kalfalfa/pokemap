"""
Calgary zones from neighbourhoods: every City of Calgary community is a zone, except that
communities smaller than MIN_KM2 are grouped with a neighbour (the one they share the longest
border with, preferring the same kind of land) until every zone is at least MIN_KM2.
"""
import csv, re, sys
from math import cos, radians
from pathlib import Path
from shapely import wkt
from shapely.ops import unary_union
from shapely.strtree import STRtree

HERE = Path(__file__).parent
csv.field_size_limit(sys.maxsize)
MIN_KM2 = 1.5
M = 1 / 111_000

def km2(g):
    c = g.centroid; return g.area * 111.32 * cos(radians(c.y)) * 110.57
def valid(g): return g if g.is_valid else g.buffer(0)

def kind(cls):
    return {"Residential": "res", "Major Park": "res", "Industrial": "ind"}.get(cls, "und")

def load():
    out = []
    for r in csv.DictReader(open(HERE / "data_calgary_communities.csv", encoding="utf-8")):
        name = r["NAME"].strip()
        undeveloped = r["CLASS"] == "Residual Sub Area" or re.fullmatch(r"\d\d[A-Z]", name)
        if undeveloped:
            name = "Undeveloped land, " + r["SECTOR"].strip().title()
        out.append({"codes": [r["COMM_CODE"].strip()], "names": [name], "g": valid(wkt.loads(r["WKT"])),
                    "kind": kind(r["CLASS"]), "cls": r["CLASS"], "undeveloped": bool(undeveloped)})
    return out

import json
from shapely.geometry import shape
try:
    RIVER = unary_union([shape(f["geometry"]) for f in json.load(open(HERE / "data_rivers.geojson"))["features"]]).buffer(60 * M)
except FileNotFoundError:
    RIVER = None

def shared(a, b):
    """Length of border two areas share, not counting stretches along the Bow River,
    so groups don't jump the river when a same-side neighbour exists."""
    edge = a.boundary.intersection(b.buffer(2 * M))
    if RIVER is not None and not edge.is_empty:
        dry = edge.difference(RIVER).length
        return dry if dry > 0 else edge.length * 0.01
    return edge.length

def group(min_km2=MIN_KM2):
    Z = load()
    for z in Z: z["a"] = km2(z["g"])
    while True:
        small = [z for z in Z if z["a"] < min_km2]
        if not small: break
        z = min(small, key=lambda q: q["a"])
        geoms = [q["g"] for q in Z]
        tree = STRtree(geoms)
        cands = []
        for j in tree.query(z["g"].buffer(3 * M)):
            q = Z[int(j)]
            if q is z: continue
            l = shared(z["g"], q["g"])
            if l > 0: cands.append((q["kind"] == z["kind"], l, q))
        if not cands:          # isolated: leave it
            z["a"] = min_km2; continue
        same = [c for c in cands if c[0]]
        pool = same if same and max(c[1] for c in same) > 0.15 * max(c[1] for c in cands) else cands
        # among neighbours with a real shared border, join the smallest, so groups stay
        # close to the minimum instead of snowballing into one big blob
        top = max(c[1] for c in pool)
        pool = [c for c in pool if c[1] >= 0.25 * top]
        _, _, q = min(pool, key=lambda c: (c[2]["a"], -c[1]))
        q["g"] = unary_union([q["g"], z["g"]]).buffer(0)
        big_first = z["a"] > q["a"] and not z["undeveloped"]
        q["codes"] += z["codes"]
        q["names"] = (z["names"] + q["names"]) if big_first else (q["names"] + z["names"])
        q["a"] = km2(q["g"])
        q["undeveloped"] = q["undeveloped"] and z["undeveloped"]
        if q["kind"] != z["kind"] and z["a"] > q["a"]: q["kind"] = z["kind"]
        Z.remove(z)
    return Z

def label(z):
    """Display name: the biggest built-up neighbourhood in the group, plus how many others."""
    names = list(dict.fromkeys(z["names"]))           # keep order, drop repeats
    built = [n for n in names if not n.startswith("Undeveloped land")]
    lead = built[0] if built else names[0]
    others = len(names) - 1
    return lead if others == 0 else f"{lead} +{others}"

if __name__ == "__main__":
    for t in (1.5, 2.0, 2.5, 3.0, 4.0):
        Z = group(t)
        a = sorted(z["a"] for z in Z)
        print(t, len(Z), "min", round(a[0], 2), "median", round(a[len(a)//2], 2), "max", round(a[-1], 1),
              "grouped", sum(1 for z in Z if len(z["names"]) > 1))
