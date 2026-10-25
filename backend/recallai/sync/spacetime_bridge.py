"""Mirror RecallAI's SQLite memory into SpacetimeDB for the live dashboard.

SQLite (memory.db) stays the primary store. This bridge:
  * pushes belongings (incl. whether they are still enrolling), sightings
    (only ones of *your* belongings), alerts and people to SpacetimeDB by calling the module's bridge-only reducers over HTTP;
  * re-pushes a row whenever it changes (e.g. Grok later fills in the
    description / place / verified columns);
  * copies alert dismissals made in the dashboard back into SQLite;
  * tags every row with its owner (the RecallAI account's user id as a
    string, "" = ownerless) so the live dashboard only shows the signed-in
    user's rows. SpacetimeDB tables are public: this is filtering for a
    local app, not access control (see docs/AUTH.md).

The reducer names and positional argument order follow SPACETIME_CONTRACT.md.

    python -m recallai.sync.spacetime_bridge          # run the sync loop (Ctrl+C to stop)
    python -m recallai.sync.spacetime_bridge --once   # one sync, print counts

server.py starts the loop in a background thread (disable with SPACETIME_SYNC=0).
If SpacetimeDB isn't running the bridge prints one line and keeps retrying
quietly; the rest of RecallAI is never affected.
"""

import argparse
import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from recallai import owners, paths
from recallai.memory import db as dbmod
from recallai.ai import llm
from recallai.vision.recognition import BELONGINGS_DIR, load_belongings

TOKEN_FILE = paths.SPACETIME_TOKEN   # .spacetime_token at the repository root
DEFAULT_URL = "http://localhost:3000"
DEFAULT_DB = "recallai"
TIMEOUT = 3          # seconds per HTTP request
INTERVAL = 1.5       # seconds between syncs
MAX_BACKOFF = 30     # seconds between retries while SpacetimeDB is down

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def config():
    """(url, database) from the environment / .env."""
    llm.load_env_file()
    url = (os.environ.get("SPACETIME_URL") or DEFAULT_URL).rstrip("/")
    name = os.environ.get("SPACETIME_DB") or DEFAULT_DB
    return url, name


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class Unreachable(Exception):
    """SpacetimeDB did not answer (not running, wrong URL, timeout)."""


class SpacetimeError(Exception):
    """SpacetimeDB answered with an error (reducer threw, unknown db, ...)."""

    def __init__(self, status, text, what=""):
        self.status = status
        self.text = (text or "").strip()
        super().__init__(f"{what}: HTTP {status}: {self.text[:300]}")


class BridgeConflict(Exception):
    """Another identity is already registered as the bridge: stop syncing."""


class BridgeLost(Exception):
    """SpacetimeDB no longer recognises us as the bridge (e.g. the module was
    republished with --delete-data): re-register and resync everything."""


REDUCER_FAILED = 530   # SpacetimeDB's status when a reducer throws


# ---------------------------------------------------------------------------
# Column mapping (SQLite -> reducer args), see SPACETIME_CONTRACT.md
# ---------------------------------------------------------------------------
def iso_to_micros(value):
    """ISO 8601 text -> int microseconds since the Unix epoch (0 if unparseable).
    Naive timestamps are treated as UTC (that's what RecallAI stores)."""
    if not value:
        return 0
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - EPOCH) // timedelta(microseconds=1)


def to_url(path):
    """'snapshots/x.jpg' -> '/snapshots/x.jpg'; '' / NULL -> ''."""
    if not path:
        return ""
    return "/" + str(path).replace("\\", "/").lstrip("/")


def _text(v):
    return "" if v is None else str(v)


def _num(v):
    """NULL / NaN / inf -> 0.0 (NaN isn't valid JSON and would be rejected)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def owner_key(value):
    """users.id -> "3"; NULL/unknown -> "" (ownerless)."""
    try:
        return owners.as_key(value)
    except (TypeError, ValueError):
        return ""


def belonging_args(item):
    """[id, name, category, photos, tracking, owner]

    tracking is False while the camera is still collecting the enrollment
    angles (info.json "tracking": false); older belongings default to True."""
    return [str(item.id), _text(item.name), _text(item.category),
            int(len(item.reference_paths)), bool(getattr(item, "tracking", True)),
            owner_key(getattr(item, "owner_id", None))]


def sighting_args(row):
    """[id, belongingId, belongingName, objectName, event, seenAtMicros,
    imageUrl, cropUrl, description, place, nearby, matchScore, verified,
    hasGps, latitude, longitude, owner]"""
    r = dict(row)
    verified = r.get("verified")
    has_gps = r.get("latitude") is not None
    return [
        int(r["id"]),
        _text(r.get("belonging_id")),
        _text(r.get("belonging_name")),
        _text(r.get("object_name")),
        _text(r.get("event")) or "seen",
        iso_to_micros(r.get("timestamp")),
        to_url(r.get("image_path")),
        to_url(r.get("crop_path")),
        _text(r.get("description")),
        _text(r.get("place")),
        _text(r.get("nearby")),
        _num(r.get("match_score")),
        -1 if verified is None else int(verified),
        has_gps,
        _num(r.get("latitude")),
        _num(r.get("longitude")),
        owner_key(r.get("owner_id")),
    ]


def alert_args(row):
    """[id, belongingId, belongingName, sightingId, reason, urgency, message,
    createdAtMicros, dismissed, owner]"""
    r = dict(row)
    return [
        int(r["id"]),
        _text(r.get("belonging_id")),
        _text(r.get("belonging_name")),
        int(r.get("sighting_id") or 0),
        _text(r.get("reason")),
        _text(r.get("urgency")),
        _text(r.get("message")),
        iso_to_micros(r.get("created_at")),
        bool(r.get("dismissed")),
        owner_key(r.get("owner_id")),
    ]


def person_args(row):
    """[id, name, description, relationship, knownSince, createdAtMicros,
    updatedAtMicros, owner]"""
    r = dict(row)
    return [
        int(r["id"]),
        _text(r.get("name")),
        _text(r.get("description")),
        _text(r.get("relationship")),
        _text(r.get("known_since")),
        iso_to_micros(r.get("created_at")),
        iso_to_micros(r.get("updated_at")),
        owner_key(r.get("owner_id")),
    ]


def sql_rows(result, column=0):
    """Pull one column out of an /sql response: a JSON array of
    {"schema": ProductType, "rows": [...]}. Rows are normally JSON arrays
    (ProductValue), but objects keyed by column name are accepted too."""
    values = []
    statements = result if isinstance(result, list) else [result]
    for stmt in statements:
        if not isinstance(stmt, dict):
            continue
        names = []
        for el in (stmt.get("schema") or {}).get("elements", []) or []:
            name = el.get("name") if isinstance(el, dict) else None
            if isinstance(name, dict):          # {"some": "id"} option encoding
                name = name.get("some")
            names.append(name)
        for row in stmt.get("rows") or []:
            if isinstance(row, (list, tuple)):
                if len(row) > column:
                    values.append(row[column])
            elif isinstance(row, dict):
                key = names[column] if len(names) > column and names[column] else None
                if key in row:
                    values.append(row[key])
                elif len(row) == 1:
                    values.append(next(iter(row.values())))
            else:
                values.append(row)              # bare scalar
    return values


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------
class SpacetimeClient:
    """Thin wrapper over SpacetimeDB's HTTP API (v1)."""

    def __init__(self, url=None, database=None, token_file=TOKEN_FILE,
                 session=None, timeout=TIMEOUT):
        env_url, env_db = config()
        self.url = (url or env_url).rstrip("/")
        self.database = database or env_db
        self.token_file = Path(token_file)
        self.session = session or requests.Session()
        self.timeout = timeout
        self.token = None
        self.identity = None

    def _post(self, path, what, **kw):
        headers = kw.pop("headers", {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            resp = self.session.post(self.url + path, headers=headers,
                                     timeout=self.timeout, **kw)
        except (requests.ConnectionError, requests.Timeout) as e:
            raise Unreachable(str(e)) from e
        if not 200 <= resp.status_code < 300:
            raise SpacetimeError(resp.status_code, resp.text, what)
        return resp

    # -- identity --------------------------------------------------------
    def load_or_create_token(self):
        """Reuse .spacetime_token, or POST /v1/identity and save a new one."""
        try:
            text = self.token_file.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text:
            try:
                saved = json.loads(text)
            except ValueError:
                saved = {"token": text}           # plain token file
            if isinstance(saved, dict) and saved.get("token"):
                self.token = saved["token"]
                self.identity = saved.get("identity")
                return self.token
        self.token = None
        data = self._post("/v1/identity", "create identity").json()
        self.token, self.identity = data["token"], data.get("identity")
        try:
            self.token_file.write_text(
                json.dumps({"identity": self.identity, "token": self.token}),
                encoding="utf-8")
        except OSError:
            pass  # still usable for this run
        return self.token

    # -- database --------------------------------------------------------
    def call(self, reducer, args):
        """POST /v1/database/{db}/call/{reducer} with a JSON array of args."""
        self._post(f"/v1/database/{self.database}/call/{reducer}", reducer,
                   data=json.dumps(list(args)),
                   headers={"Content-Type": "application/json"})

    def sql(self, query):
        """POST /v1/database/{db}/sql with the raw SQL text as body."""
        resp = self._post(f"/v1/database/{self.database}/sql", "sql",
                          data=query.encode("utf-8"),
                          headers={"Content-Type": "text/plain"})
        return resp.json()


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------
class SpacetimeBridge:
    def __init__(self, url=None, database=None, db_path=None,
                 belongings_dir=None, token_file=TOKEN_FILE, session=None,
                 interval=INTERVAL, log=None, client=None):
        self.client = client or SpacetimeClient(url, database, token_file, session)
        self.db_path = db_path            # None -> dbmod.DB_PATH at call time
        self.belongings_dir = belongings_dir
        self.interval = interval
        self.log = log or (lambda msg: print(msg, flush=True))
        self.registered = False
        self._stop = threading.Event()
        self._thread = None
        self._failed_rows = set()
        self.reset()

    @property
    def where(self):
        return f"{self.client.url} (database '{self.client.database}')"

    def reset(self):
        """Forget what was pushed, so the next sync re-sends everything."""
        self.pushed = {"belonging": {}, "sighting": {}, "alert": {}, "person": {}}
        self._prune_belongings = True   # after (re)connect: delete rows that
        self._prune_rows = True         # vanished from disk/SQLite meanwhile

    # -- connect ---------------------------------------------------------
    def connect(self):
        """Get a token and register as the bridge (idempotent)."""
        self.client.load_or_create_token()
        try:
            self.client.call("register_bridge", [])
        except SpacetimeError as e:
            if "different bridge" in e.text.lower():
                raise BridgeConflict(
                    f"SpacetimeDB database '{self.client.database}' already has "
                    "a different bridge registered (it was published or synced "
                    "by someone else). Dashboard sync stopped. Fix: republish "
                    "with `spacetime publish --delete-data ...`, or restore the "
                    f"original {self.client.token_file.name}.") from e
            if e.status == 401:
                raise BridgeConflict(
                    f"SpacetimeDB rejected the saved token in "
                    f"{self.client.token_file.name} (HTTP 401). Delete that file "
                    "and republish the module with --delete-data.") from e
            raise
        self.registered = True
        self.reset()

    # -- one sync pass ---------------------------------------------------
    def _db(self):
        return dbmod.connect(self.db_path)

    def _push(self, kind, key, reducer, args):
        """Call reducer if args changed since the last push. Returns 1/0."""
        fp = tuple(args)
        if self.pushed[kind].get(key) == fp:
            return 0
        try:
            self.client.call(reducer, args)
        except SpacetimeError as e:
            if e.status == REDUCER_FAILED and "only the recallai bridge" in e.text.lower():
                # Data was wiped (republish --delete-data): register again.
                self.registered = False
                raise BridgeLost(e.text) from e
            if e.status != REDUCER_FAILED:
                raise   # transient (5xx, 4xx): the loop retries with a full resync
            # The reducer rejected this row's data. One bad row must not block
            # the rest; remember it so we don't retry (and log) it every 1.5 s
            # until it changes.
            if (kind, key) not in self._failed_rows:
                self.log(f"[SPACETIME] {reducer}({key}) rejected: {e.text[:200]}")
                self._failed_rows.add((kind, key))
            self.pushed[kind][key] = fp
            return 0
        self._failed_rows.discard((kind, key))
        self.pushed[kind][key] = fp
        return 1

    def sync_once(self):
        """Push changes to SpacetimeDB, pull dismissals back. Returns counts."""
        if not self.registered:
            self.connect()
        counts = {"belongings": 0, "deleted_belongings": 0, "sightings": 0,
                  "alerts": 0, "people": 0, "dismissed": 0}

        # Was the database wiped since our last pass (republish --delete-data)?
        # Then nothing we pushed is there any more and our bridge registration
        # is gone too, even if no write has failed yet. One tiny query per pass.
        if self.pushed["belonging"]:
            remote = {str(v) for v in sql_rows(
                self.client.sql("SELECT id FROM belonging"))}
            if not remote & set(self.pushed["belonging"]):
                self.registered = False
                raise BridgeLost("belongings we pushed are gone from SpacetimeDB")

        # Belongings (from disk)
        items = load_belongings(self.belongings_dir or BELONGINGS_DIR)
        current = set()
        for item in items:
            current.add(str(item.id))
            counts["belongings"] += self._push(
                "belonging", str(item.id), "upsert_belonging", belonging_args(item))
        stale = set(self.pushed["belonging"]) - current
        if self._prune_belongings:
            # After a (re)connect also remove ones deleted while we were away.
            try:
                stale |= {str(v) for v in sql_rows(
                    self.client.sql("SELECT id FROM belonging"))} - current
                self._prune_belongings = False   # only once it actually worked
            except SpacetimeError:
                pass
        for bid in sorted(stale):
            self.client.call("delete_belonging", [bid])
            self.pushed["belonging"].pop(bid, None)
            counts["deleted_belongings"] += 1

        with self._db() as db:
            sightings = db.execute(
                "SELECT * FROM sightings WHERE belonging_id IS NOT NULL "
                "AND belonging_id != '' ORDER BY id").fetchall()
            alerts = db.execute("SELECT * FROM alerts ORDER BY id").fetchall()
            try:
                people = db.execute("SELECT * FROM people ORDER BY id").fetchall()
            except sqlite3.OperationalError:
                people = None   # memory.db not upgraded yet (no init_db): skip

        for row in sightings:
            counts["sightings"] += self._push(
                "sighting", int(row["id"]), "upsert_sighting", sighting_args(row))
        for row in alerts:
            counts["alerts"] += self._push(
                "alert", int(row["id"]), "upsert_alert", alert_args(row))
        for row in people or []:
            counts["people"] += self._push(
                "person", int(row["id"]), "upsert_person", person_args(row))

        # Rows deleted from SQLite (e.g. memory.db reset) -> delete them in
        # SpacetimeDB too, so the dashboard never shows ghosts.
        counts["deleted_sightings"] = self._prune(
            "sighting", {int(r["id"]) for r in sightings}, "delete_sighting")
        counts["deleted_alerts"] = self._prune(
            "alert", {int(r["id"]) for r in alerts}, "delete_alert")
        if people is not None:
            counts["deleted_people"] = self._prune(
                "person", {int(r["id"]) for r in people}, "delete_person")
        self._prune_rows = False

        # Dismissals made in the dashboard -> SQLite
        ids = []
        for v in sql_rows(self.client.sql(
                "SELECT id FROM alert WHERE dismissed = true")):
            try:
                ids.append(int(v))
            except (TypeError, ValueError):
                pass
        if ids:
            with self._db() as db:
                marks = ",".join("?" for _ in ids)
                cur = db.execute(
                    f"UPDATE alerts SET dismissed=1 WHERE dismissed=0 "
                    f"AND id IN ({marks})", ids)
                db.commit()
                counts["dismissed"] = cur.rowcount
        return counts

    def _prune(self, kind, current, reducer):
        stale = set(self.pushed[kind]) - current
        if self._prune_rows:
            remote = set()
            for v in sql_rows(self.client.sql(f"SELECT id FROM {kind}")):
                try:
                    remote.add(int(v))
                except (TypeError, ValueError):
                    pass
            stale |= remote - current
        for rid in sorted(stale):
            self.client.call(reducer, [rid])
            self.pushed[kind].pop(rid, None)
        return len(stale)

    # -- background loop -------------------------------------------------
    def run(self):
        down = False          # already told the user it's unreachable
        backoff = self.interval
        while not self._stop.is_set():
            try:
                self.sync_once()
                if down:
                    self.log(f"[SPACETIME] Connected to {self.where}; "
                             "dashboard sync resumed")
                down = False
                backoff = self.interval
            except BridgeConflict as e:
                self.log(f"[SPACETIME] {e}")
                return
            except BridgeLost:
                self.log("[SPACETIME] SpacetimeDB data was reset; "
                         "re-registering and resyncing everything")
                self.registered = False   # next pass re-registers + resyncs
            except Unreachable:
                if not down:
                    self.log(f"[SPACETIME] SpacetimeDB not running at "
                             f"{self.client.url}; dashboard sync paused, retrying")
                down = True
                self.registered = False   # re-register + full resync later
                backoff = min(max(backoff * 2, self.interval), MAX_BACKOFF)
            except Exception as e:  # never kill the server's thread
                if not down:
                    if isinstance(e, SpacetimeError) and e.status == 404:
                        msg = (f"database '{self.client.database}' not found at "
                               f"{self.client.url} (publish the module in spacetimedb/)")
                    else:
                        msg = f"sync problem with {self.where}: {e}"
                    self.log(f"[SPACETIME] {msg}; dashboard sync paused, retrying")
                down = True
                self.registered = False
                backoff = min(max(backoff * 2, self.interval), MAX_BACKOFF)
            self._stop.wait(backoff if down else self.interval)

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="spacetime-bridge",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout=None):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--once", action="store_true", help="sync once and exit")
    args = p.parse_args(argv)
    dbmod.init_db()
    bridge = SpacetimeBridge()
    if args.once:
        try:
            counts = bridge.sync_once()
        except Unreachable:
            print(f"SpacetimeDB not running at {bridge.client.url}")
            return 1
        except (BridgeConflict, SpacetimeError) as e:
            print(e)
            return 1
        print("Synced to", bridge.where + ":",
              ", ".join(f"{k}={v}" for k, v in counts.items()))
        return 0
    print(f"Mirroring memory.db -> SpacetimeDB at {bridge.where} (Ctrl+C to stop)")
    bridge.start()
    try:
        while bridge._thread.is_alive():
            bridge._thread.join(1)
    except KeyboardInterrupt:
        bridge.stop(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
