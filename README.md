# Scan Zones

A public map where players tap the areas of Calgary, Airdrie and CrossIron Mills they want the local Pokémon scanner to cover. Each browser gets an anonymous ID, so a player's picks are remembered and each player counts once. The Overview tab shows how many unique players have voted and shades every area by demand, so the scan areas can be chosen fairly instead of favouring whoever subscribed most recently.

## What's in the folder

| File | Purpose |
|---|---|
| `server.py` | The whole backend. Python 3.8+ standard library only. Serves the page and stores picks in SQLite. |
| `index.html` | The page, **generated** by `build.py`. Map data, Leaflet and the CARTO key are embedded. |
| `areas.geojson` | The selectable areas, **generated** by `build.py`. The server reads it to validate picks. |
| `build.py` | Rebuilds `areas.geojson` and `index.html`. Needs `shapely`. |
| `neighbourhoods.py` | How Calgary areas are made: one per community, small ones grouped (`MIN_KM2`). |
| `app.template.html` | Page source (HTML, CSS, JS) that `build.py` fills in. Edit this, not `index.html`. |
| `carto_key.txt` | CARTO Basemaps API key (first line). Not in version control. |
| `admin_token.txt` | Token that enables `/api/export.csv` (first line). Not in version control. |
| `data_*` | Source data, see "Data sources" below. |
| `fallback_airdrie_boundary_2011.geojson` | Rough Airdrie outline used until a current one is added. |
| `vendor/` | Leaflet 1.9.4 (JS and CSS), inlined into the page at build time. |
| `tools/` | One-off tools: the Bow River tracer, and the archived road-based zone layout. |
| `CLAUDE.md` | Working notes for continuing development with Claude Code. |

## Run it

```
python3 server.py
# open http://localhost:8080
```

Settings are environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | 8080 | Listen port |
| `HOST` | 0.0.0.0 | Listen address. Use 127.0.0.1 behind a proxy. |
| `DB` | `./scanzones.db` | SQLite file holding all picks |
| `TRUST_PROXY` | off | Set to `1` behind Caddy / nginx / Cloudflare so real visitor IPs are used |
| `ADMIN_TOKEN` | from `admin_token.txt` | Enables `GET /api/export.csv?token=...`, a CSV of every pick |
| `CARTO_KEY` | from `carto_key.txt` | CARTO Basemaps API key; read at build time and embedded in `index.html` |
| `SALT` | random, stored in the DB | Secret used to hash IP addresses |
| `QUIET` | off | `1` turns off request logging |

## Rebuild the page

Needed after changing the areas, the page template, or the CARTO key.

```
pip install shapely
python3 build.py
```

The CARTO key is read from `carto_key.txt` (or the `CARTO_KEY` environment variable, which wins if both are present) and baked into `index.html`. Without one, the map shows "API key required" tiles. Get or manage keys at https://carto.com/basemaps/apikey/ and, once the site is live, restrict the key to your domain in CARTO's dashboard (after that it won't load tiles from a local file). `index.html` and `areas.geojson` are build outputs; rebuild them on the host rather than committing them.

The admin token is read from `admin_token.txt` (or the `ADMIN_TOKEN` environment variable). Both files are gitignored.

## Put it on the internet

**Cloudflare Tunnel** (no open ports, free HTTPS): `cloudflared tunnel --url http://localhost:8080` for a quick test URL, or a named tunnel on your own domain. Run the server with `TRUST_PROXY=1`.

**Caddy** (automatic HTTPS) on a machine with ports 80/443 forwarded:

```
scanzones.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Run the server with `HOST=127.0.0.1 TRUST_PROXY=1`.

### systemd unit

```
# /etc/systemd/system/scanzones.service
[Unit]
Description=Scan Zones
After=network.target

[Service]
WorkingDirectory=/opt/scanzones
Environment=HOST=127.0.0.1 PORT=8080 TRUST_PROXY=1 DB=/var/lib/scanzones/votes.db QUIET=1
ExecStart=/usr/bin/python3 /opt/scanzones/server.py
Restart=on-failure
DynamicUser=yes
StateDirectory=scanzones

[Install]
WantedBy=multi-user.target
```

`sudo systemctl enable --now scanzones`

**Backups:** the SQLite file named by `DB` is the entire dataset. Copy it nightly (`sqlite3 votes.db ".backup votes-$(date +%F).db"` is safe while the server runs).

## How picks are tracked

- On first visit the browser creates a random ID (stored in localStorage). No accounts.
- Each change sends the player's full list of picks: `PUT /api/me {"uid", "areas"}`. The server replaces that player's list.
- `GET /api/me?uid=` returns a player's saved picks so they reappear on return visits.
- `GET /api/stats` returns unique players, distinct networks (hashed IPs), players per area and "share of vote" per area (each player's one vote split across their picks). Cached for 5 seconds.
- Only area IDs present in `areas.geojson` are accepted. Writes are limited to 40 per minute per IP.

Anonymous IDs are a trust system: clearing browser data or switching devices makes a new player. If "players" is far above "networks", someone may be voting from several browsers.

**Lock the areas before launch.** Picks are stored by area ID. Changing `MIN_KM2`, the source data or the Airdrie boundary changes IDs, and picks on changed areas stop counting.

## Data sources

| File | Source |
|---|---|
| `data_calgary_communities.csv` | City of Calgary Open Data, Community District Boundaries (surr-xmvs). The Calgary areas. |
| `data_rivers.geojson` | Bow River, traced along those community boundaries by `tools/trace_rivers.py` |
| `data_calgary_speed_limits.csv` | City of Calgary speed limits (via a public GitHub mirror, ~2019). Only used to sketch major roads if map tiles fail. |
| `fallback_airdrie_boundary_2011.geojson` | Statistics Canada census subdivision (2011), via Government of Alberta open data. Outdated and simplified. |

To use a current Airdrie boundary, download the municipal boundary from https://data-airdrie.opendata.arcgis.com as GeoJSON, save it as `data_airdrie_boundary.geojson`, and run `build.py`.

Map tiles: © OpenStreetMap contributors, © CARTO.
