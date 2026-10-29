#!/usr/bin/env python3
"""
Scan Zones server. Python 3.8+ standard library only (no pip installs).

  python3 server.py                      # http://0.0.0.0:8080
  PORT=9000 DB=/var/lib/scanzones/votes.db python3 server.py

Environment
  PORT           listen port (default 8080)
  HOST           listen address (default 0.0.0.0)
  DB             SQLite file (default ./scanzones.db)
  TRUST_PROXY    "1" if behind a reverse proxy / tunnel that sets X-Forwarded-For
  ADMIN_TOKEN    if set (env or admin_token.txt), GET /api/export.csv?token=... downloads every pick
  SALT           secret used to hash IPs (default: random per DB, stored in it)

API
  GET  /api/me?uid=<uuid>        -> {"known": bool, "areas": [...]}
  PUT  /api/me  {"uid","areas"}  -> {"ok": true, "areas": [...]}
  GET  /api/stats                -> {"users","networks","counts","shares","groups","updated"}
  GET  /api/export.csv?token=    -> CSV (only if ADMIN_TOKEN is set)
"""
import csv, gzip, hashlib, io, json, os, re, secrets, sqlite3, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "8080"))
HOST = os.environ.get("HOST", "0.0.0.0")
DB_PATH = os.environ.get("DB", str(HERE / "scanzones.db"))
TRUST_PROXY = os.environ.get("TRUST_PROXY") == "1"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
if not ADMIN_TOKEN and (HERE / "admin_token.txt").exists():
    ADMIN_TOKEN = ((HERE / "admin_token.txt").read_text().strip().splitlines() or [""])[0].strip()
MAX_PICKS = 400
UID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,18}[a-z0-9]$")
PIN_RE = re.compile(r"^[0-9]{4}$")

INDEX = (HERE / "index.html").read_bytes()
VENDOR = {
    "leaflet.css": ((HERE / "vendor" / "leaflet.css").read_bytes(), "text/css; charset=utf-8"),
    "leaflet.js": ((HERE / "vendor" / "leaflet.js").read_bytes(), "application/javascript; charset=utf-8"),
}
AREA_GROUP = {f["id"]: f["properties"].get("group", "") for f in json.loads((HERE / "areas.geojson").read_text("utf-8"))["features"]}
AREA_IDS = set(AREA_GROUP)

# ---------------- storage ----------------
db_lock = threading.Lock()
db = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
db.execute("PRAGMA journal_mode=WAL")
db.executescript("""
CREATE TABLE IF NOT EXISTS users (
  uid TEXT PRIMARY KEY, ip_hash TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS picks (
  uid TEXT NOT NULL, area TEXT NOT NULL, PRIMARY KEY (uid, area));
CREATE INDEX IF NOT EXISTS picks_area ON picks(area);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
""")
for col in ("username TEXT", "pin_hash TEXT"):
    try:
        db.execute(f"ALTER TABLE users ADD COLUMN {col}")
    except sqlite3.OperationalError:
        pass
db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username ON users(username) WHERE username IS NOT NULL")
row = db.execute("SELECT v FROM meta WHERE k='salt'").fetchone()
SALT = os.environ.get("SALT") or (row[0] if row else None)
if not SALT:
    SALT = secrets.token_hex(16)
    db.execute("INSERT INTO meta VALUES ('salt', ?)", (SALT,))

def ip_hash(ip):
    return hashlib.sha256((SALT + ip).encode()).hexdigest()[:16]

def pin_hash(username, pin):
    salt = (SALT + "pin:" + username).encode()
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 50000).hex()

# ---------------- stats cache ----------------
_stats = {"at": 0.0, "body": b""}
def stats_body():
    now = time.time()
    if now - _stats["at"] < 5 and _stats["body"]:
        return _stats["body"]
    with db_lock:
        per_user = {}
        for uid, area in db.execute("SELECT uid, area FROM picks"):
            per_user.setdefault(uid, []).append(area)
        nets = db.execute(
            "SELECT COUNT(DISTINCT ip_hash) FROM users WHERE uid IN (SELECT DISTINCT uid FROM picks)").fetchone()[0]
    counts, shares, groups = {}, {}, {}
    for areas in per_user.values():
        for g in {AREA_GROUP.get(a, "") for a in areas} - {""}:
            groups[g] = groups.get(g, 0) + 1
        w = 1.0 if len(areas) <= 10 else 10.0 / len(areas)
        for a in areas:
            counts[a] = counts.get(a, 0) + 1
            shares[a] = shares.get(a, 0.0) + w
    body = json.dumps({
        "users": len(per_user), "networks": nets, "counts": counts,
        "shares": {k: round(v, 3) for k, v in shares.items()}, "groups": groups,
        "updated": datetime.now(timezone.utc).isoformat(),
    }, separators=(",", ":")).encode()
    _stats.update(at=now, body=body)
    return body

# ---------------- rate limiting (per IP, writes) ----------------
_hits = {}
def allow(ip, limit=40, window=60):
    now = time.time()
    q = [t for t in _hits.get(ip, []) if now - t < window]
    if len(q) >= limit:
        _hits[ip] = q
        return False
    q.append(now)
    _hits[ip] = q
    if len(_hits) > 50000:
        _hits.clear()
    return True

# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = "ScanZones/1.0"

    def client_ip(self):
        if TRUST_PROXY:
            for h in ("CF-Connecting-IP", "X-Real-IP"):
                if self.headers.get(h):
                    return self.headers[h].strip()
            xff = self.headers.get("X-Forwarded-For")
            if xff:
                return xff.split(",")[0].strip()
        return self.client_address[0]

    def send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        extra = dict(extra or {})
        compressible = ctype.startswith("text/") or "json" in ctype or "javascript" in ctype
        if compressible and len(body) >= 1024 and "gzip" in self.headers.get("Accept-Encoding", ""):
            body = gzip.compress(body, compresslevel=6)
            extra["Content-Encoding"] = "gzip"
            extra["Vary"] = "Accept-Encoding"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if "Cache-Control" not in extra:
            self.send_header("Cache-Control", "no-store" if ctype.startswith("application/json") else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, code, obj):
        self.send(code, json.dumps(obj, separators=(",", ":")).encode())

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self.send(200, INDEX, "text/html; charset=utf-8")
        if u.path.startswith("/vendor/"):
            name = u.path[len("/vendor/"):]
            if name in VENDOR:
                body, ctype = VENDOR[name]
                return self.send(200, body, ctype, {"Cache-Control": "public, max-age=604800"})
            return self.json(404, {"error": "not found"})
        if u.path == "/api/me":
            uid = (parse_qs(u.query).get("uid") or [""])[0].lower()
            if not UID_RE.match(uid):
                return self.json(400, {"error": "bad uid"})
            with db_lock:
                row = db.execute("SELECT username FROM users WHERE uid=?", (uid,)).fetchone()
                areas = [r[0] for r in db.execute("SELECT area FROM picks WHERE uid=?", (uid,))]
            return self.json(200, {"known": row is not None, "username": (row[0] if row else None), "areas": areas})
        if u.path == "/api/stats":
            return self.send(200, stats_body())
        if u.path == "/api/export.csv":
            tok = (parse_qs(u.query).get("token") or [""])[0]
            if not ADMIN_TOKEN or not secrets.compare_digest(tok, ADMIN_TOKEN):
                return self.json(404, {"error": "not found"})
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["user", "network", "area", "user_first_seen", "user_last_change"])
            with db_lock:
                for r in db.execute("""SELECT substr(u.uid,1,8), u.ip_hash, p.area, u.created, u.updated
                                       FROM picks p JOIN users u USING(uid) ORDER BY u.created"""):
                    w.writerow([r[0], r[1], r[2],
                                datetime.fromtimestamp(r[3], timezone.utc).isoformat(timespec="seconds"),
                                datetime.fromtimestamp(r[4], timezone.utc).isoformat(timespec="seconds")])
            return self.send(200, buf.getvalue().encode(), "text/csv; charset=utf-8",
                             {"Content-Disposition": 'attachment; filename="scanzones-picks.csv"'})
        if u.path == "/healthz":
            return self.send(200, b"ok", "text/plain")
        self.json(404, {"error": "not found"})

    def read_json(self, limit=4096):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n <= 0 or n > limit:
                return None
            return json.loads(self.rfile.read(n))
        except Exception:
            return None

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/claim":
            data = self.read_json() or {}
            uid = str(data.get("uid", "")).lower()
            username = str(data.get("username", "")).strip().lower()
            pin = str(data.get("pin", ""))
            if not UID_RE.match(uid) or not USERNAME_RE.match(username) or not PIN_RE.match(pin):
                return self.json(400, {"error": "bad request"})
            if not allow(self.client_ip()):
                return self.json(429, {"error": "slow down"})
            h = pin_hash(username, pin)
            now = time.time()
            with db_lock:
                existing = db.execute("SELECT uid FROM users WHERE username=?", (username,)).fetchone()
                if existing and existing[0] != uid:
                    return self.json(409, {"error": "username taken"})
                db.execute("BEGIN")
                db.execute("""INSERT INTO users(uid, ip_hash, created, updated, username, pin_hash)
                              VALUES (?,?,?,?,?,?)
                              ON CONFLICT(uid) DO UPDATE SET updated=excluded.updated,
                                  username=excluded.username, pin_hash=excluded.pin_hash""",
                           (uid, ip_hash(self.client_ip()), now, now, username, h))
                db.execute("COMMIT")
            return self.json(200, {"ok": True, "username": username})
        if p == "/api/resume":
            if not allow(self.client_ip()):
                return self.json(429, {"error": "slow down"})
            data = self.read_json() or {}
            username = str(data.get("username", "")).strip().lower()
            pin = str(data.get("pin", ""))
            if not USERNAME_RE.match(username) or not PIN_RE.match(pin):
                return self.json(400, {"error": "bad request"})
            h = pin_hash(username, pin)
            with db_lock:
                row = db.execute("SELECT uid, pin_hash FROM users WHERE username=?", (username,)).fetchone()
                ok = row and row[1] and secrets.compare_digest(row[1], h)
                if not ok:
                    return self.json(403, {"error": "wrong username or PIN"})
                areas = [r[0] for r in db.execute("SELECT area FROM picks WHERE uid=?", (row[0],))]
            return self.json(200, {"uid": row[0], "username": username, "areas": areas})
        return self.json(404, {"error": "not found"})

    def do_PUT(self):
        if urlparse(self.path).path != "/api/me":
            return self.json(404, {"error": "not found"})
        ip = self.client_ip()
        if not allow(ip):
            return self.json(429, {"error": "slow down"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n <= 0 or n > 64 * 1024:
                raise ValueError
            data = json.loads(self.rfile.read(n))
            uid = str(data["uid"]).lower()
            areas = sorted({str(a) for a in data["areas"]} & AREA_IDS)
            if not UID_RE.match(uid) or len(areas) > MAX_PICKS:
                raise ValueError
        except Exception:
            return self.json(400, {"error": "bad request"})
        now = time.time()
        with db_lock:
            db.execute("BEGIN")
            db.execute("""INSERT INTO users(uid, ip_hash, created, updated) VALUES (?,?,?,?)
                          ON CONFLICT(uid) DO UPDATE SET ip_hash=excluded.ip_hash, updated=excluded.updated""",
                       (uid, ip_hash(ip), now, now))
            db.execute("DELETE FROM picks WHERE uid=?", (uid,))
            db.executemany("INSERT INTO picks(uid, area) VALUES (?,?)", [(uid, a) for a in areas])
            db.execute("COMMIT")
        _stats["at"] = 0
        self.json(200, {"ok": True, "areas": areas})

    def log_message(self, fmt, *args):
        if os.environ.get("QUIET") != "1":
            super().log_message(fmt, *args)

if __name__ == "__main__":
    print(f"Scan Zones on http://{HOST}:{PORT}  ({len(AREA_IDS)} areas, db {DB_PATH})")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
