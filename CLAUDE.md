# CLAUDE.md: Scan Zones

Notes for continuing this project in Claude Code. Read README.md for deployment details.

## What this is

A community voting map for a local Pokémon scanner covering Calgary, Airdrie and CrossIron Mills. Players tap the areas they want scanned; the Overview shows unique players and demand per area. The goal is fairness: new subscribers currently bias which areas get scanned, and this lets every player count once.

## Conventions

- **Version control is git.** The maintainer does all commits; never commit on their behalf. `git add` any new files that belong under version control.
- Secrets stay out of the repo. The CARTO Basemaps key lives in `carto_key.txt` (one line) and `build.py` reads it; the admin export token lives in `admin_token.txt` and `server.py` reads it. The `CARTO_KEY` / `ADMIN_TOKEN` environment variables override the files when set. Both files, plus the SQLite database, are gitignored. See `.gitignore`.
- `index.html` and `areas.geojson` are generated. Edit `app.template.html`, `neighbourhoods.py` or `build.py`, then run `python3 build.py`. These outputs are gitignored and rebuilt on the host.
- UI copy uses Canadian spelling ("neighbourhood") and typographic apostrophes/quotes in visible text.

## Architecture

```
build.py ──reads──> data_calgary_communities.csv, data_rivers.geojson, Airdrie boundary,
   │                data_calgary_speed_limits.csv, vendor/leaflet.*, carto_key.txt
   │  uses neighbourhoods.py (grouping)
   ├──writes──> areas.geojson   (validated against by server.py)
   └──writes──> index.html      (template + inlined data, Leaflet, CARTO key)

server.py (stdlib only) ── serves index.html, /api/me (GET/PUT), /api/stats,
                           /api/export.csv (ADMIN_TOKEN), /healthz; SQLite storage
```

- **Frontend** (`app.template.html`): Leaflet map, two tabs (Pick areas / Overview), hash routes `#pick` and `#overview`. Picks are cached in localStorage under `scanzones:v1` along with the anonymous `uid`. If `api/me` doesn't answer (e.g. opened as a local file), the page runs in "preview" mode: picks saved locally only, Overview shows only your own picks.
- **Basemap**: CARTO raster tiles, Voyager (light) / Dark Matter (dark), loaded as a no-labels base layer plus a labels-only layer in a pane above the area polygons so street names stay readable. URLs are `https://basemaps.cartocdn.com/rastertiles/{voyager|dark}_{nolabels|only_labels}/{z}/{x}/{y}{r}.png?key=KEY`. CARTO now requires a key; without it tiles show an "API key required" watermark.
- **Fallback roads**: if no tile ever loads, the page draws major roads from embedded City data so the map isn't blank.
- **Labels**: area names appear only at zoom ≥ 13 (`zoomed-in` class); Airdrie and CrossIron labels always show. Selected areas turn gold.
- **Backend** (`server.py`): tables `users(uid, ip_hash, created, updated, username, pin_hash)`, `picks(uid, area)`, `meta(salt)`. A PUT replaces a user's whole pick list. Stats cached 5 s. Rate limit 40 writes/min/IP. IPs stored only as salted SHA-256 prefixes. Unique "networks" = distinct ip_hash among voters. Concurrency: `ThreadingHTTPServer` with per-thread sqlite connections cached in `threading.local()`, WAL journal mode, `PRAGMA busy_timeout=5000`, and `BEGIN IMMEDIATE` for writes — no Python-side lock. (An earlier version used a single shared connection behind a global `threading.Lock`, which could wedge the whole app if one handler got stuck holding it while `/healthz` kept returning 200.)

## Current area design

- **Calgary**: one area per City of Calgary community (313 communities, including industrial land and undeveloped "residual" areas, so the whole city is covered). Communities under `MIN_KM2 = 1.5` km² are grouped with a neighbour. Result: ~224 Calgary areas.
  - Grouping (`neighbourhoods.py`): repeatedly take the smallest under-size area and merge it into the **smallest** neighbour that shares a real border (≥25% of the longest shared border), preferring the same land type (residential/park vs industrial vs undeveloped). Picking the smallest neighbour prevents snowballing. Borders along the Bow River count ~1%, so groups don't jump the river if a same-side neighbour exists.
  - Names: biggest built-up community first, then "+N" for the rest (e.g. "Downtown Commercial Core +4"). Undeveloped residual areas are named "Undeveloped land, <Sector>".
  - Area IDs: `C-` + sorted community codes joined with `-`.
  - Size options measured: 1.5 km² → 224 areas, 2 → 199, 3 → 152, 4 → 126.
- **Airdrie**: one single area. Currently uses the 2011 StatCan outline (10 vertices, pre-2012 annexation), which is too rough. **Needs the current municipal boundary** from data-airdrie.opendata.arcgis.com saved as `data_airdrie_boundary.geojson`; `build.py` picks it up automatically.
- **CrossIron Mills**: one hand-drawn rectangle around the mall and Century Downs (SE of QEII & Hwy 566, mall at about 51.204, -113.992), minus any overlap with Calgary.

### History of what was tried (so it isn't re-proposed)

1. Communities as individual areas, Airdrie as a 3×3 grid → the maintainer wanted voting subdivisions, then wards.
2. Ward-based zones (14) → "terrible jigsaw shapes".
3. Zones built from whole communities seeded by wards (balanced k-means style), first excluding industrial land → the maintainer wants **full city coverage, including industrial**.
4. Road- and river-bounded zones (~15), hand-grouped from road cells → cleaner, but the maintainer then chose neighbourhoods with a minimum size and **many more than 14 areas**. This approach is archived in `tools/road_zones/` (`zones.py`, `zone_layout.json`, 2017 ward sample, 2018 traffic volumes, city boundary).
5. The maintainer asked to cut off "the arm" in the far NW (Haskayne along the Bow valley). With the return to neighbourhoods, Haskayne is back as its own area; raise this with the maintainer if edges come up again.

## Data notes and limits

- **Sandbox limits during development**: data.calgary.ca, OpenStreetMap/Overpass and ArcGIS services were unreachable from the build environment, so some data came from GitHub mirrors. On the production host these sources are likely reachable directly, which is a chance to refresh data:
  - Calgary Community District Boundaries: dataset `surr-xmvs` (CSV export with WKT). The bundled copy is from a 2026 mirror and looks current (313 communities).
  - City Ward Boundaries `tz8z-hyaz` and Voting Subdivisions `rrgu-vddg` are no longer needed.
  - Airdrie open data (ArcGIS Hub, org `bctnJobT0aahg98G` on services1.arcgis.com): `MunicipalBoundary` FeatureServer and `Neighbourhoods_Airdrie` FeatureServer. A query like `.../FeatureServer/0/query?where=1%3D1&outFields=*&outSR=4326&f=geojson` should return GeoJSON directly.
- `data_rivers.geojson` (Bow only) was traced by `tools/trace_rivers.py`, which needs the meshflow `bcalgary_rivers.shp`. The Elbow River isn't reliably a community boundary, so it isn't traced.
- `data_calgary_speed_limits.csv` is ~2019 data; it's only for the tile-failure fallback sketch.

## Gotchas

- **Chrome focus box**: clicking an SVG path focuses it and draws a rectangle around its bounding box. Fixed with `.leaflet-interactive:focus { outline: none; }`. Keep that rule.
- **Picks are keyed by area ID.** Any change to `MIN_KM2`, community data or the Airdrie boundary changes IDs. Before launch that's fine; after launch, write a migration (map old IDs to new by overlap) or accept lost picks.
- `server.py` reads `areas.geojson` at startup; restart it after rebuilding.
- The CARTO key is embedded in `index.html` and visible to players (normal for map keys). Restrict it to the site's domain in CARTO's dashboard once live; a domain-restricted key won't work from `file://`. Because the key ends up in `index.html`, that file is gitignored — rebuild on the host instead of committing it.
- CARTO free tier: 1M requests/month for commercial use (5M non-commercial). Each visit loads two tile layers, roughly 50-150 tiles.

## Testing

- `python3 build.py` should print the Calgary area count, the Airdrie source file and the page size.
- Smoke test the API:
  ```
  DB=/tmp/t.db python3 server.py &
  U=11111111-1111-4111-8111-111111111111
  curl -s "localhost:8080/api/me?uid=$U"
  curl -s -X PUT localhost:8080/api/me -H 'Content-Type: application/json' \
       -d "{\"uid\":\"$U\",\"areas\":[\"AIRDRIE\",\"CROSSIRON\"]}"
  curl -s localhost:8080/api/stats
  ```
- UI checks used during development (Playwright, headless Chromium): load `#pick`, search "renfrew", click the result, confirm it turns gold and appears under "Your areas"; switch to `#overview` and confirm counts; click a polygon and confirm the active element has `outline: none`.

## Open items

1. Add the current Airdrie municipal boundary (`data_airdrie_boundary.geojson`) and rebuild.
2. Confirm the final Calgary minimum size (currently 1.5 km²) with the maintainer, then lock areas.
3. Deploy: systemd + Caddy or Cloudflare Tunnel (see README), `TRUST_PROXY=1`, nightly DB backup.
4. Restrict the CARTO key to the production domain.
5. Optional ideas not yet requested: subscriber codes to stop multi-browser voting; an admin view of the CSV export; showing the "Overview" link prominently for sharing.
