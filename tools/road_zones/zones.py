"""
ARCHIVED (not used by build.py). Earlier approach: ~15 Calgary zones bounded by major roads
and the Bow River. Kept in case the road-based layout is wanted again; see CLAUDE.md.

Calgary scan zones, bounded by major roads and the Bow River.

  1. A skeleton of major roads (named freeways and arterials, plus the busiest unnamed
     segments from the City's traffic counts) and the Bow River cuts the city into cells.
  2. Cells on the city edge that are mostly undeveloped land are trimmed off, so the outer
     edge follows the built-up city instead of every bump in the legal boundary.
  3. Cells are grouped into compact zones of similar size (residential land counts fully,
     industrial land less, undeveloped land very little), starting from the ward layout.
"""
import csv, json, re, sys
from math import cos, radians
from pathlib import Path
from shapely import wkt
from shapely.geometry import shape, Polygon
from shapely.ops import unary_union, polylabel
from shapely.strtree import STRtree
import networkx as nx

HERE = Path(__file__).resolve().parent          # this folder: city boundary, traffic volumes, layout
ROOT = HERE.parents[1]                          # project root: speed limits, rivers, communities
csv.field_size_limit(sys.maxsize)
K = 0.6293                       # cos(51.05 deg)
M = 1 / 111_000                  # metres -> degrees (approx.)
CUT_M = 20                       # half-width of the road/river cut
MIN_CELL_KM2 = 0.8
THIN_KM2, THIN_Q = 5, 0.18          # long thin slivers get merged too
BALANCE = 1.0e-5                 # how hard zone sizes are evened out (vs. compactness)
ADJ_M = 120                      # cells this close across a road count as neighbours
VOLUME_MIN = 30000               # unnamed roads join the skeleton above this daily volume
SKELETON = ["DEERFOOT TR", "CROWCHILD TR", "GLENMORE TR", "16 AV", "TRANS CANADA HI", "STONEY TR",
            "MACLEOD TR", "MCKNIGHT BV", "COUNTRY HILLS BV", "JOHN LAURIE BV", "SARCEE TR",
            "SHAGANAPPI TR", "ANDERSON RD", "52 ST", "MEMORIAL DR", "BOW TR", "17 AV", "BLACKFOOT TR",
            "BARLOW TR", "36 ST", "METIS TR", "BEDDINGTON TR", "SPRUCE MEADOWS TR", "14 ST", "37 ST",
            "SOUTHLAND DR", "HARVEST HILLS BV", "SYMONS VALLEY RD", "NOSE HILL DR", "PEIGAN TR"]
# hand-trimmed edge pieces: the arm along the Bow valley west of Tuscany
CLIP_COMMUNITIES = ["Haskayne", "01F"]
WEIGHT = {"Residential": 1.0, "Major Park": 0.7, "Industrial": 0.7, "Residual Sub Area": 0.25}
TRIM_M = 1300                    # edge bumps narrower than ~2.6 km are trimmed

def km2(g):
    c = g.centroid; return g.area * 111.32 * cos(radians(c.y)) * 110.57
def parts(g): return list(g.geoms) if hasattr(g, "geoms") else [g]
def valid(g): return g if g.is_valid else g.buffer(0)
def label_point(g):
    big = max(parts(g), key=lambda p: p.area)
    p = polylabel(big, tolerance=0.0002); return [round(p.y, 5), round(p.x, 5)]

def city_outline():
    return valid(wkt.loads(next(csv.DictReader(open(HERE / "data_calgary_city_boundary.csv")))["the_geom"]))

def skeleton():
    rx = re.compile("^(" + "|".join(re.escape(s) for s in SKELETON) + r")( [NSEW]{1,2})?$")
    lines = [wkt.loads(r["multiline"]) for r in csv.DictReader(open(ROOT / "data_calgary_speed_limits.csv"))
             if rx.match(r["STREET_NAME"].strip())]
    lines += [wkt.loads(r["multilinestring"]) for r in csv.DictReader(open(HERE / "data_calgary_traffic_volumes.csv"))
              if r["multilinestring"] and (r["VOLUME"] or "0").isdigit() and int(r["VOLUME"]) >= VOLUME_MIN]
    lines += [shape(f["geometry"]) for f in json.load(open(ROOT / "data_rivers.geojson"))["features"]]
    return unary_union(lines)

def adjacency(cells):
    G = nx.Graph(); G.add_nodes_from(range(len(cells)))
    grown = [c.buffer(ADJ_M * M) for c in cells]
    tree = STRtree(grown)
    for i, g in enumerate(grown):
        for j in tree.query(g):
            j = int(j)
            if j > i and g.intersects(grown[j]):
                a = g.intersection(grown[j]).area
                if a > 0: G.add_edge(i, j, w=a)
    return G

def build(seed_points, communities, anchors=None):
    """seed_points: [(lat, lon)] one per zone; communities: [(name, cls, geom)]"""
    city = city_outline()
    cells = [p for p in parts(city.difference(skeleton().buffer(CUT_M * M))) if p.area > 0]
    # land-use weight per cell
    ctree = STRtree([g for *_, g in communities])
    def weigh(p):
        w, und, res = 0.0, 0.0, 0.0
        for j in ctree.query(p):
            name, cls, g = communities[int(j)]
            if not g.intersects(p): continue
            a = g.intersection(p).area
            w += WEIGHT.get(cls, 0.3) * a
            if cls == "Residual Sub Area": und += a
            if cls == "Residential": res += a
        return w, und, res
    # merge slivers into the neighbour they share most with
    from shapely import affinity
    from math import pi
    def roundness(g):
        q = affinity.scale(g, xfact=K, yfact=1, origin=(0, 0))
        return 4 * pi * q.area / (q.length ** 2) if q.length else 1
    while True:
        G = adjacency(cells)
        small = [i for i in range(len(cells)) if G.degree(i) and
                 (km2(cells[i]) < MIN_CELL_KM2 or (km2(cells[i]) < THIN_KM2 and roundness(cells[i]) < THIN_Q))]
        if not small: break
        i = min(small, key=lambda k: cells[k].area)
        j = max(G[i], key=lambda k: G[i][k]["w"])
        cells[j] = unary_union([cells[j], cells[i]]); cells.pop(i)
    # trim edge cells that are mostly undeveloped land
    edge = city.boundary.buffer(60 * M)
    keep, dropped = [], []
    for c in cells:
        w, und, res = weigh(c)
        if c.intersects(edge) and und > 0.8 * c.area and res < 0.05 * c.area:
            dropped.append(c); continue
        keep.append({"g": c, "w": w})
    C = keep
    G = adjacency([c["g"] for c in C])

    for c in C:
        p = c["g"].representative_point(); c["x"], c["y"] = p.x * K, p.y
    body = city.difference(unary_union(dropped).buffer((CUT_M + 2) * M, join_style="mitre")) if dropped else city
    body = Polygon(max(parts(body), key=lambda p: p.area).exterior).intersection(city)
    t = TRIM_M * M
    body = body.buffer(-t, join_style="mitre").buffer(t, join_style="mitre").intersection(body)
    body = max(parts(body), key=lambda p: p.area)
    # never trim away a built-up neighbourhood (other than the hand-clipped arm)
    homes = unary_union([g for name, cls, g in communities if cls == "Residential" and name not in CLIP_COMMUNITIES])
    body = unary_union([body, homes.intersection(city)]).buffer(0)
    body = max(parts(body), key=lambda p: p.area)
    clip = unary_union([g for name, _, g in communities if name in CLIP_COMMUNITIES])
    if not clip.is_empty:
        body = body.difference(clip.buffer(30 * M, join_style="mitre"))
    body = max(parts(body), key=lambda p: p.area)
    for c in C:
        c["g"] = c["g"].intersection(body)
    C = [c for c in C if not c["g"].is_empty and c["g"].area > 0]
    if anchors is not None:
        # hand layout: each cell joins the zone whose anchor point it contains (else the nearest anchor)
        from shapely.geometry import Point
        pts = [[Point(lon, lat) for lat, lon in grp] for grp in anchors]
        for c in C:
            hit = [i for i, grp in enumerate(pts) if any(c["g"].contains(q) for q in grp)]
            if hit: c["z"] = hit[0]
            else:
                c["z"] = min(range(len(pts)), key=lambda i: min(((q.x - c["x"] / K) * K) ** 2 + (q.y - c["y"]) ** 2 for q in pts[i]))
        return finish(C, body, len(anchors))
    seeds = [(lon * K, lat) for lat, lon in seed_points]
    n = len(seeds); target = sum(c["w"] for c in C) / n
    off = [0.0] * n
    def assign():
        for c in C:
            c["z"] = min(range(n), key=lambda i: (seeds[i][0] - c["x"]) ** 2 + (seeds[i][1] - c["y"]) ** 2 - off[i])
    for it in range(800):
        assign()
        load = [0.0] * n
        for c in C: load[c["z"]] += c["w"]
        for i in range(n): off[i] += BALANCE * (target - load[i]) / target
        if it % 20 == 19:
            for i in range(n):
                mem = [c for c in C if c["z"] == i]
                if mem:
                    W = sum(c["w"] for c in mem) or 1
                    cx = sum(c["x"] * c["w"] for c in mem) / W; cy = sum(c["y"] * c["w"] for c in mem) / W
                    seeds[i] = (0.6 * seeds[i][0] + 0.4 * cx, 0.6 * seeds[i][1] + 0.4 * cy)
    assign()
    for _ in range(30):     # contiguity
        moved = False
        for i in range(n):
            mem = [k for k, c in enumerate(C) if c["z"] == i]
            comps = list(nx.connected_components(G.subgraph(mem)))
            if len(comps) <= 1: continue
            mainc = max(comps, key=lambda s: sum(C[k]["w"] for k in s))
            for comp in comps:
                if comp is mainc: continue
                for k in comp:
                    nb = [C[j]["z"] for j in G[k] if C[j]["z"] != i]
                    if nb: C[k]["z"] = max(set(nb), key=nb.count); moved = True
        if not moved: break
    return finish(C, body, n)

def finish(C, body, n):
    """Turn cell groups into zone polygons that tile the outline: each zone grows across the
    road strips around it (nearest zone wins), then leftovers join the zone they touch most."""
    raw = [unary_union([c["g"] for c in C if c["z"] == i]) for i in range(n)]
    zones, taken = [], Polygon()
    grow = 75 * M
    # grow in rounds so neighbours split each road strip roughly down the middle
    cur = [r for r in raw]
    for step in (CUT_M + 3, 45, 75):
        nxt, taken = [], unary_union([z for z in cur if not z.is_empty])
        for i in range(n):
            if cur[i].is_empty: nxt.append(cur[i]); continue
            others = unary_union([cur[j] for j in range(n) if j != i and not cur[j].is_empty])
            g = cur[i].buffer(step * M, join_style="mitre").intersection(body).difference(others)
            nxt.append(unary_union([cur[i], g]))
        # resolve overlaps from this round: first come, first served
        fixed, acc = [], Polygon()
        for z in nxt:
            z = z.difference(acc) if not z.is_empty else z
            fixed.append(z); acc = unary_union([acc, z])
        cur = fixed
    zones = cur
    rest = body.difference(unary_union([z for z in zones if not z.is_empty]))
    for p in parts(rest):
        if p.is_empty or p.area <= 0: continue
        i = max(range(n), key=lambda k: zones[k].buffer(3 * M).intersection(p).area if not zones[k].is_empty else -1)
        zones[i] = unary_union([zones[i], p])
    out = []
    for z in zones:
        if z.is_empty: continue
        z = z.buffer(2 * M, join_style="mitre").buffer(-2 * M, join_style="mitre").buffer(0)
        z = unary_union([q.buffer(0) for q in parts(z) if q.geom_type == "Polygon" and q.area > 0])
        out.append(z)
    # fill holes that no other zone occupies (slits left by dead-end roads)
    for i, z in enumerate(out):
        others = unary_union([o for k, o in enumerate(out) if k != i])
        filled = unary_union([Polygon(q.exterior) for q in parts(z)])
        gaps = filled.difference(z).difference(others)
        if not gaps.is_empty: out[i] = unary_union([z, gaps])
    # tiny detached bits go to the neighbour they touch most
    for i, z in enumerate(out):
        if z.geom_type != "MultiPolygon": continue
        gs = list(z.geoms)
        k0 = max(range(len(gs)), key=lambda k: gs[k].area)
        for k, q in enumerate(gs):
            if k == k0: continue
            j = max((k for k in range(len(out)) if k != i), key=lambda k: out[k].buffer(5 * M).intersection(q).area)
            if out[j].buffer(5 * M).intersection(q).area > 0:
                out[j] = unary_union([out[j], q]); z = z.difference(q)
        out[i] = z
    return body, out, C


if __name__ == "__main__":
    # quick look: python3 tools/road_zones/zones.py  -> /tmp/road_zones.png
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    comms = [(r["NAME"], r["CLASS"], valid(wkt.loads(r["WKT"]))) for r in csv.DictReader(open(ROOT / "data_calgary_communities.csv"))]
    layout = json.load(open(HERE / "zone_layout.json"))
    body, zones, _ = build(None, comms, layout)
    fig, ax = plt.subplots(figsize=(10, 12))
    for i, z in enumerate(zones):
        for q in parts(z):
            x, y = q.exterior.xy; ax.fill(x, y, alpha=.5); ax.plot(x, y, "k", lw=.8)
        lp = label_point(z); ax.text(lp[1], lp[0], str(i), ha="center")
    ax.set_aspect(1 / K); plt.savefig("/tmp/road_zones.png", dpi=60); print("wrote /tmp/road_zones.png")
