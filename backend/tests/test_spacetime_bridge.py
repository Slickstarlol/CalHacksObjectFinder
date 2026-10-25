"""spacetime_bridge.py against a fake SpacetimeDB HTTP server (no network)."""

import json

import pytest
import requests
from fastapi.testclient import TestClient

from recallai.memory import db as dbmod
from recallai.sync import spacetime_bridge as sb
from conftest import RED, make_object_image, register

URL = "http://stdb.test:3000"


class FakeResponse:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        return self._body if self._body is not None else json.loads(self.text)


class FakeSpacetime:
    """Stands in for requests.Session; records every POST."""

    def __init__(self):
        self.calls = []          # (path, headers, body)
        self.down = False
        self.dismissed = []      # alert ids the "dashboard" dismissed
        self.belonging_ids = []  # rows of `SELECT id FROM belonging`
        self.sighting_ids = []   # rows of `SELECT id FROM sighting`
        self.alert_ids = []      # rows of `SELECT id FROM alert`
        self.person_ids = []     # rows of `SELECT id FROM person`
        self.status = {}         # reducer -> (status, text) for one-off errors
        self.remote_belongings = set()   # what upsert/delete_belonging left behind
        self.register_error = None
        self.reject = {}         # reducer -> error text
        self.identities = 0

    def post(self, url, headers=None, data=None, timeout=None, **kw):
        assert timeout and timeout <= 5
        if self.down:
            raise requests.ConnectionError("refused")
        assert url.startswith(URL)
        path = url[len(URL):]
        self.calls.append((path, dict(headers or {}), data))
        if path == "/v1/identity":
            self.identities += 1
            return FakeResponse(200, {"identity": f"c200abc{self.identities}",
                                      "token": f"tok{self.identities}"})
        if path == "/v1/database/recallai/sql":
            q = data.decode() if isinstance(data, bytes) else data
            if "FROM alert WHERE dismissed" in q:
                rows = [[i] for i in self.dismissed]
            elif "FROM alert" in q:
                rows = [[i] for i in self.alert_ids]
            elif "FROM sighting" in q:
                rows = [[i] for i in self.sighting_ids]
            elif "FROM person" in q:
                rows = [[i] for i in self.person_ids]
            else:
                rows = [[i] for i in sorted(set(self.belonging_ids) | self.remote_belongings)]
            return FakeResponse(200, [{"schema": {"elements": [
                {"name": {"some": "id"}, "algebraic_type": {"U64": []}}]},
                "rows": rows, "total_duration_micros": 5}])
        reducer = path.rsplit("/", 1)[-1]
        if reducer in ("upsert_belonging", "delete_belonging") and not (
                reducer in self.reject or reducer in self.status):
            bid = json.loads(data)[0]
            if reducer == "upsert_belonging":
                self.remote_belongings.add(bid)
            else:
                self.remote_belongings.discard(bid)
        if reducer == "register_bridge" and self.register_error:
            return FakeResponse(530, text=self.register_error)
        if reducer in self.reject:
            return FakeResponse(530, text=self.reject[reducer])
        if reducer in self.status:
            st, text = self.status.pop(reducer)
            return FakeResponse(st, text=text)
        return FakeResponse(200, text="")

    # helpers
    def reducer_calls(self, name=None):
        out = []
        for path, headers, body in self.calls:
            if "/call/" in path:
                r = path.rsplit("/", 1)[-1]
                if name is None or r == name:
                    out.append((r, json.loads(body)))
        return out

    def clear(self):
        self.calls.clear()


@pytest.fixture
def world(tmp_path, belongings_dir, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "m.db")
    dbmod.init_db()
    register(belongings_dir, "My Bag", "Backpack",
             [make_object_image(RED, seed=i) for i in range(3)], item_id="bag1")
    register(belongings_dir, "My Mouse", "mouse", [], item_id="mouse1")
    fake = FakeSpacetime()
    logs = []
    bridge = sb.SpacetimeBridge(url=URL, database="recallai",
                                belongings_dir=belongings_dir,
                                token_file=tmp_path / ".spacetime_token",
                                session=fake, interval=0.01, log=logs.append)

    class W:
        pass
    w = W()
    w.dir, w.fake, w.bridge, w.logs, w.tmp = belongings_dir, fake, bridge, logs, tmp_path
    return w


FULL = dict(object_name="backpack", latitude=37.87, longitude=-122.26, accuracy=10,
            timestamp="2026-10-24T15:00:00.250000+00:00",
            image_path="snapshots/1_bag_seen.jpg", belonging_id="bag1",
            belonging_name="My Bag", event="seen", confidence=0.8, match_score=0.91,
            bbox="1,2,3,4", crop_path="snapshots/crops/1_bag_seen.jpg",
            nearby="laptop, bottle", verified=1, verify_reason="yes",
            description="On the gray table", place="hackathon table")


def add_sighting(**over):
    fields = dict(FULL, **over)
    with dbmod.connect() as db:
        return dbmod.insert_sighting(db, **fields)


def add_alert(sighting_id=None, dismissed=0, created="2026-10-24T08:00:00-07:00"):
    with dbmod.connect() as db:
        cur = db.execute(
            "INSERT INTO alerts (belonging_id, belonging_name, sighting_id, reason,"
            " urgency, message, created_at, dismissed) VALUES (?,?,?,?,?,?,?,?)",
            ("bag1", "My Bag", sighting_id, "out_of_sight", "high",
             "Did you forget My Bag?", created, dismissed))
        db.commit()
        return cur.lastrowid


# ------------------------------------------------------------ pure mapping
@pytest.mark.parametrize("text,micros", [
    ("1970-01-01T00:00:00+00:00", 0),
    ("1970-01-01T00:00:01Z", 1_000_000),
    ("2026-10-24T15:00:00+00:00", 1792854000_000000),
    ("2026-10-24T08:00:00-07:00", 1792854000_000000),   # same instant
    ("2026-10-24T15:00:00.250000+00:00", 1792854000_250000),
    ("2026-10-24T15:00:00", 1792854000_000000),          # naive = UTC
    ("garbage", 0), (None, 0), ("", 0),
])
def test_iso_to_micros(text, micros):
    assert sb.iso_to_micros(text) == micros
    assert isinstance(sb.iso_to_micros(text), int)


def test_to_url():
    assert sb.to_url("snapshots/a.jpg") == "/snapshots/a.jpg"
    assert sb.to_url("snapshots\\crops\\a.jpg") == "/snapshots/crops/a.jpg"
    assert sb.to_url(None) == "" and sb.to_url("") == ""


def test_sql_rows_formats():
    assert sb.sql_rows([{"schema": {"elements": []}, "rows": [[1], [2]]}]) == [1, 2]
    obj = [{"schema": {"elements": [{"name": {"some": "id"}}]},
            "rows": [{"id": 7}]}]
    assert sb.sql_rows(obj) == [7]
    assert sb.sql_rows([{"schema": {}, "rows": []}]) == []


# ------------------------------------------------------------ HTTP shape
def test_token_created_then_reused(world):
    world.bridge.sync_once()
    token_file = world.tmp / ".spacetime_token"
    saved = json.loads(token_file.read_text())
    assert saved == {"identity": "c200abc1", "token": "tok1"}
    path, headers, _ = world.fake.calls[0]
    assert path == "/v1/identity" and "Authorization" not in headers
    # every later request carries the bearer token
    for path, headers, _ in world.fake.calls[1:]:
        assert headers["Authorization"] == "Bearer tok1"
    assert world.fake.calls[1][0] == "/v1/database/recallai/call/register_bridge"
    assert json.loads(world.fake.calls[1][2]) == []

    # a new bridge (e.g. server restart) reuses the file, no new identity
    fake2 = FakeSpacetime()
    b2 = sb.SpacetimeBridge(url=URL, database="recallai", belongings_dir=world.dir,
                            token_file=token_file, session=fake2, log=world.logs.append)
    b2.sync_once()
    assert fake2.identities == 0
    assert all(h["Authorization"] == "Bearer tok1" for _, h, _ in fake2.calls)


def test_plain_token_file_accepted(world):
    (world.tmp / ".spacetime_token").write_text("rawtoken\n")
    world.bridge.sync_once()
    assert world.fake.identities == 0
    assert world.fake.calls[0][1]["Authorization"] == "Bearer rawtoken"


def test_belongings_pushed_in_contract_order(world):
    world.bridge.sync_once()
    calls = world.fake.reducer_calls("upsert_belonging")
    assert calls == [("upsert_belonging", ["bag1", "My Bag", "backpack", 3, True]),
                     ("upsert_belonging", ["mouse1", "My Mouse", "mouse", 0, True])]
    assert type(calls[0][1][4]) is bool
    # sql bodies are raw text with the right path
    sql = [(p, b) for p, _, b in world.fake.calls if p.endswith("/sql")]
    assert sql and all(p == "/v1/database/recallai/sql" for p, _ in sql)
    assert b"SELECT id FROM alert WHERE dismissed = true" in [b for _, b in sql]


def test_upsert_sighting_all_16_args_full_row(world):
    sid = add_sighting()
    world.bridge.sync_once()
    [(_, args)] = world.fake.reducer_calls("upsert_sighting")
    assert len(args) == 16
    expected = [
        (sid, int),                               # id
        ("bag1", str),                            # belongingId
        ("My Bag", str),                          # belongingName
        ("backpack", str),                        # objectName
        ("seen", str),                            # event
        (1792854000_250000, int),                 # seenAtMicros
        ("/snapshots/1_bag_seen.jpg", str),       # imageUrl
        ("/snapshots/crops/1_bag_seen.jpg", str), # cropUrl
        ("On the gray table", str),               # description
        ("hackathon table", str),                 # place
        ("laptop, bottle", str),                  # nearby
        (0.91, float),                            # matchScore
        (1, int),                                 # verified
        (True, bool),                             # hasGps
        (37.87, float),                           # latitude
        (-122.26, float),                         # longitude
    ]
    for i, (value, typ) in enumerate(expected):
        assert args[i] == value, i
        assert type(args[i]) is typ, (i, type(args[i]))


def test_upsert_sighting_nulls(world):
    with dbmod.connect() as db:
        sid = dbmod.insert_sighting(
            db, object_name="mouse", timestamp="2026-10-24T15:00:00Z",
            image_path="snapshots/m.jpg", belonging_id="mouse1",
            belonging_name=None, event="left_view")
    world.bridge.sync_once()
    [(_, args)] = world.fake.reducer_calls("upsert_sighting")
    assert args == [sid, "mouse1", "", "mouse", "left_view", 1792854000_000000,
                    "/snapshots/m.jpg", "", "", "", "", 0.0, -1, False, 0.0, 0.0]
    assert type(args[11]) is float and type(args[12]) is int
    assert args[13] is False


def test_verified_zero_is_synced(world):
    add_sighting(verified=0)
    world.bridge.sync_once()
    [(_, args)] = world.fake.reducer_calls("upsert_sighting")
    assert args[12] == 0


def test_generic_sightings_not_synced(world):
    with dbmod.connect() as db:
        dbmod.insert_sighting(db, object_name="backpack",
                              timestamp="2026-10-24T15:00:00+00:00",
                              image_path="snapshots/old.jpg")
    world.bridge.sync_once()
    assert world.fake.reducer_calls("upsert_sighting") == []


def test_upsert_alert_args(world):
    sid = add_sighting()
    a1 = add_alert(sighting_id=sid)
    a2 = add_alert(sighting_id=None, dismissed=1, created="2026-10-24T15:00:00+00:00")
    world.bridge.sync_once()
    calls = world.fake.reducer_calls("upsert_alert")
    assert calls == [
        ("upsert_alert", [a1, "bag1", "My Bag", sid, "out_of_sight", "high",
                          "Did you forget My Bag?", 1792854000_000000, False]),
        ("upsert_alert", [a2, "bag1", "My Bag", 0, "out_of_sight", "high",
                          "Did you forget My Bag?", 1792854000_000000, True]),
    ]
    assert type(calls[0][1][8]) is bool


# ------------------------------------------------------------ change tracking
def test_only_changed_rows_repushed_and_grok_update(world):
    s1 = add_sighting()
    s2 = add_sighting(timestamp="2026-10-24T15:01:00+00:00", description=None,
                      place=None, verified=None)
    add_alert(sighting_id=s1)
    world.bridge.sync_once()
    world.fake.clear()

    world.bridge.sync_once()                       # nothing changed
    assert world.fake.reducer_calls() == []

    with dbmod.connect() as db:                    # Grok interprets s2 later
        db.execute("UPDATE sightings SET description=?, place=?, verified=1 "
                   "WHERE id=?", ("Under the chair", "kitchen", s2))
        db.commit()
    s3 = add_sighting(timestamp="2026-10-24T15:02:00+00:00")
    world.bridge.sync_once()
    calls = world.fake.reducer_calls("upsert_sighting")
    assert [a[0] for _, a in calls] == [s2, s3]
    assert calls[0][1][8:10] == ["Under the chair", "kitchen"]
    assert calls[0][1][12] == 1
    assert world.fake.reducer_calls("upsert_belonging") == []
    assert world.fake.reducer_calls("upsert_alert") == []


def test_deleted_belonging_triggers_delete(world):
    import shutil
    world.bridge.sync_once()
    world.fake.clear()
    shutil.rmtree(world.dir / "mouse1")
    world.bridge.sync_once()
    assert world.fake.reducer_calls("delete_belonging") == [
        ("delete_belonging", ["mouse1"])]
    world.fake.clear()
    world.bridge.sync_once()
    assert world.fake.reducer_calls() == []


def test_stale_belonging_in_spacetime_deleted_on_connect(world):
    world.fake.belonging_ids = ["bag1", "old_item"]
    world.bridge.sync_once()
    assert world.fake.reducer_calls("delete_belonging") == [
        ("delete_belonging", ["old_item"])]


def test_photo_count_change_repushes_belonging(world):
    world.bridge.sync_once()
    world.fake.clear()
    register(world.dir, "My Mouse", "mouse", [make_object_image(RED)], item_id="mouse1")
    world.bridge.sync_once()
    assert world.fake.reducer_calls("upsert_belonging") == [
        ("upsert_belonging", ["mouse1", "My Mouse", "mouse", 1, True])]


def test_rejected_row_does_not_block_others(world):
    add_sighting()
    add_alert()
    world.fake.reject["upsert_sighting"] = "unknown event"
    world.bridge.sync_once()
    world.bridge.sync_once()
    assert len(world.fake.reducer_calls("upsert_alert")) == 1
    assert sum("rejected" in l for l in world.logs) == 1


# ------------------------------------------------------------ dismissals
def test_dismissal_copied_back_to_sqlite(world):
    a1 = add_alert()
    a2 = add_alert(created="2026-10-24T09:00:00-07:00")
    world.bridge.sync_once()
    world.fake.dismissed = [a1]
    counts = world.bridge.sync_once()
    assert counts["dismissed"] == 1
    with dbmod.connect() as db:
        rows = dict(db.execute("SELECT id, dismissed FROM alerts").fetchall())
    assert rows == {a1: 1, a2: 0}
    counts = world.bridge.sync_once()               # idempotent
    assert counts["dismissed"] == 0


# ------------------------------------------------------------ failures
def test_unreachable_logs_once_and_recovers(world):
    add_sighting()
    world.fake.down = True
    world.bridge.start()
    import time
    time.sleep(0.3)
    assert world.bridge._thread.is_alive()
    assert len(world.logs) == 1
    assert "SpacetimeDB not running at http://stdb.test:3000" in world.logs[0]
    assert "retrying" in world.logs[0]

    world.fake.down = False
    deadline = time.time() + 5
    while not world.fake.reducer_calls("upsert_sighting") and time.time() < deadline:
        time.sleep(0.02)
    world.bridge.stop(2)
    assert world.fake.reducer_calls("upsert_sighting")
    assert world.fake.reducer_calls("register_bridge")
    assert any("resumed" in l for l in world.logs)


def test_reconnect_resyncs_everything(world):
    add_sighting()
    world.bridge.sync_once()
    world.fake.down = True
    with pytest.raises(sb.Unreachable):
        world.bridge.sync_once()
    world.bridge.registered = False     # what run() does on Unreachable
    world.fake.down = False
    world.fake.clear()
    world.bridge.sync_once()
    names = [r for r, _ in world.fake.reducer_calls()]
    assert names[0] == "register_bridge"
    assert "upsert_sighting" in names and "upsert_belonging" in names


def test_other_bridge_registered_stops(world):
    world.fake.register_error = "A different bridge is already registered"
    world.bridge.start()
    world.bridge._thread.join(2)
    assert not world.bridge._thread.is_alive()
    assert len(world.logs) == 1
    assert "different bridge" in world.logs[0]
    assert "--delete-data" in world.logs[0]
    assert world.fake.reducer_calls("upsert_belonging") == []


def test_config_defaults(monkeypatch):
    monkeypatch.delenv("SPACETIME_URL", raising=False)
    monkeypatch.delenv("SPACETIME_DB", raising=False)
    monkeypatch.setattr(sb.llm, "load_env_file", lambda *a, **k: [])
    assert sb.config() == ("http://localhost:3000", "recallai")
    monkeypatch.setenv("SPACETIME_URL", "http://h:1/")
    monkeypatch.setenv("SPACETIME_DB", "x")
    assert sb.config() == ("http://h:1", "x")


def test_cli_once(world, monkeypatch, capsys):
    monkeypatch.setattr(sb, "SpacetimeBridge", lambda: world.bridge)
    add_sighting()
    assert sb.main(["--once"]) == 0
    out = capsys.readouterr().out
    assert "sightings=1" in out and "belongings=2" in out


# ------------------------------------------------------------ server wiring
class FakeWatcher:
    def __init__(self, *a, **k):
        pass

    def start(self):
        return self


class RecordingBridge:
    started = 0
    stopped = 0
    where = "fake"

    def start(self):
        RecordingBridge.started += 1

    def stop(self, timeout=None):
        RecordingBridge.stopped += 1


@pytest.fixture
def server_mod(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "m.db")
    from recallai.api import server
    dbmod.init_db()
    monkeypatch.setattr(server, "Watcher", FakeWatcher)
    monkeypatch.setattr(server, "SpacetimeBridge", RecordingBridge)
    RecordingBridge.started = RecordingBridge.stopped = 0
    return server


def test_lifespan_skips_bridge_when_disabled(server_mod, monkeypatch, capsys):
    monkeypatch.setenv("SPACETIME_SYNC", "0")
    with TestClient(server_mod.app) as c:
        assert c.get("/api/alerts").status_code == 200
    assert RecordingBridge.started == 0
    assert "Dashboard sync: off" in capsys.readouterr().out


def test_lifespan_starts_bridge(server_mod, monkeypatch):
    monkeypatch.delenv("SPACETIME_SYNC", raising=False)
    with TestClient(server_mod.app):
        assert RecordingBridge.started == 1
    assert RecordingBridge.stopped == 1


def test_server_routes_unaffected_and_cors(server_mod, monkeypatch):
    monkeypatch.setattr(server_mod.agent, "ask",
                        lambda q, h, tz: {"answer": "On the table [#1]",
                                          "sightings": [], "steps": []})
    c = TestClient(server_mod.app)
    r = c.post("/api/ask", json={"question": "where is my bag?"},
               headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 200 and r.json()["answer"].startswith("On the table")
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    pre = c.options("/api/ask", headers={
        "Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert pre.status_code == 200
    r = c.get("/api/alerts", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    assert c.get("/gps/latest").status_code == 200
    assert c.get("/").status_code == 200


# ---------------------------------------------------------------- review fixes
def test_ghost_rows_in_spacetime_are_deleted_on_connect(world):
    """memory.db was reset: SpacetimeDB still has old sighting/alert ids."""
    sid = add_sighting()
    world.fake.sighting_ids = [sid, 900, 901]
    world.fake.alert_ids = [77]
    counts = world.bridge.sync_once()
    assert world.fake.reducer_calls("delete_sighting") == [
        ("delete_sighting", [900]), ("delete_sighting", [901])]
    assert world.fake.reducer_calls("delete_alert") == [("delete_alert", [77])]
    assert counts["deleted_sightings"] == 2 and counts["deleted_alerts"] == 1
    world.fake.clear()
    world.bridge.sync_once()          # remote lookup only right after connect
    assert world.fake.reducer_calls("delete_sighting") == []


def test_row_deleted_from_sqlite_while_running_is_deleted_remotely(world):
    keep, gone = add_sighting(), add_sighting(event="left_view")
    world.bridge.sync_once()
    with dbmod.connect() as db:
        db.execute("DELETE FROM sightings WHERE id=?", (gone,))
        db.commit()
    world.fake.clear()
    world.bridge.sync_once()
    assert world.fake.reducer_calls("delete_sighting") == [("delete_sighting", [gone])]
    assert keep in world.bridge.pushed["sighting"]


def test_bridge_reregisters_after_republish_with_delete_data(world):
    sid = add_sighting()
    world.bridge.sync_once()
    add_sighting(event="left_view")
    world.fake.status["upsert_sighting"] = (
        530, "Only the RecallAI bridge can write memory data")
    world.fake.clear()
    with pytest.raises(sb.BridgeLost):
        world.bridge.sync_once()
    assert world.bridge.registered is False
    world.fake.clear()
    world.bridge.sync_once()          # re-registers and resends EVERYTHING
    assert world.fake.reducer_calls("register_bridge") == [("register_bridge", [])]
    assert [a[0] for _, a in world.fake.reducer_calls("upsert_sighting")] == [sid, sid + 1]


def test_run_loop_recovers_from_lost_bridge(world):
    add_sighting()
    world.fake.status["upsert_sighting"] = (
        530, "Only the RecallAI bridge can write memory data")
    import threading
    t = threading.Thread(target=world.bridge.run)
    t.start()
    import time
    deadline = time.time() + 3
    while time.time() < deadline and len(world.fake.reducer_calls("upsert_sighting")) < 2:
        time.sleep(0.01)
    world.bridge.stop(2)
    t.join(2)
    assert len(world.fake.reducer_calls("register_bridge")) >= 2
    assert any("re-registering" in l for l in world.logs)


def test_transient_error_is_retried_not_skipped(world):
    sid = add_sighting()
    world.fake.status["upsert_sighting"] = (500, "worker busy")
    with pytest.raises(sb.SpacetimeError):
        world.bridge.sync_once()
    assert sid not in world.bridge.pushed["sighting"]
    world.bridge.sync_once()
    assert [a[0] for _, a in world.fake.reducer_calls("upsert_sighting")][-1] == sid
    assert sid in world.bridge.pushed["sighting"]


def test_nan_and_inf_become_zero():
    assert sb._num(float("nan")) == 0.0 and sb._num(float("inf")) == 0.0
    assert sb._num(None) == 0.0 and sb._num("0.5") == 0.5


def test_idle_wipe_is_detected_without_any_failed_write(world):
    """republish --delete-data while nothing changes in SQLite: the bridge must
    still notice (its pushed belongings vanished) and resync everything."""
    add_sighting()
    world.bridge.sync_once()
    world.fake.remote_belongings.clear()      # the wipe
    world.fake.clear()
    with pytest.raises(sb.BridgeLost):
        world.bridge.sync_once()
    world.bridge.sync_once()
    assert world.fake.reducer_calls("register_bridge") == [("register_bridge", [])]
    assert len(world.fake.reducer_calls("upsert_sighting")) == 1
    assert len(world.fake.reducer_calls("upsert_belonging")) == 2


# ------------------------------------------------------------ enrollment (tracking)
def test_enrolling_belonging_synced_with_tracking_false_then_flips(world):
    """enroll.py writes info.json with "tracking": false until all angles are
    saved; the dashboard needs that to show "Enrolling..." instead of "Not seen"."""
    from recallai.vision import enroll
    img = make_object_image(RED, seed=9)
    info, n = enroll.save_angle(world.dir, "cup", img)
    assert n == 1 and info["tracking"] is False
    world.bridge.sync_once()
    calls = {a[0]: a for _, a in world.fake.reducer_calls("upsert_belonging")}
    assert calls[info["id"]] == [info["id"], info["name"], "cup", 1, False]
    assert calls["bag1"][4] is True                    # no key in info.json -> tracking

    for _ in range(enroll.ANGLES - 1):
        info, n = enroll.save_angle(world.dir, "cup", img, belonging_id=info["id"])
    assert info["tracking"] is True
    world.fake.clear()
    world.bridge.sync_once()
    assert world.fake.reducer_calls("upsert_belonging") == [
        ("upsert_belonging", [info["id"], info["name"], "cup", enroll.ANGLES, True])]


# ------------------------------------------------------------ people
def add_person(name="Chawin", known_since="2024-09-01", description="Glasses",
               relationship="teammate"):
    with dbmod.connect() as db:
        cur = db.execute(
            "INSERT INTO people (name, description, relationship, known_since,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (name, description, relationship, known_since,
             "2026-10-24T15:00:00+00:00", "2026-10-24T15:00:00.000001+00:00"))
        db.commit()
        return cur.lastrowid


def test_upsert_person_args_in_contract_order(world):
    p1 = add_person()
    p2 = add_person(name="Kent", known_since=None, description="", relationship="")
    counts = world.bridge.sync_once()
    assert counts["people"] == 2
    assert world.fake.reducer_calls("upsert_person") == [
        ("upsert_person", [p1, "Chawin", "Glasses", "teammate", "2024-09-01",
                           1792854000_000000, 1792854000_000001]),
        ("upsert_person", [p2, "Kent", "", "", "", 1792854000_000000,
                           1792854000_000001]),
    ]


def test_person_edit_repushed_and_delete_pruned(world):
    keep, gone = add_person(), add_person(name="Old friend")
    world.bridge.sync_once()
    world.fake.clear()
    world.bridge.sync_once()
    assert world.fake.reducer_calls() == []            # unchanged -> nothing sent

    with dbmod.connect() as db:                        # PUT /api/people/{id}
        db.execute("UPDATE people SET relationship='friend', "
                   "updated_at='2026-10-24T16:00:00+00:00' WHERE id=?", (keep,))
        db.execute("DELETE FROM people WHERE id=?", (gone,))
        db.commit()
    counts = world.bridge.sync_once()
    [(_, args)] = world.fake.reducer_calls("upsert_person")
    assert args[0] == keep and args[3] == "friend"
    assert world.fake.reducer_calls("delete_person") == [("delete_person", [gone])]
    assert counts["deleted_people"] == 1


def test_people_via_api_reach_spacetime(world):
    """The real people router (Chawin's /api/people) feeds the bridge."""
    from recallai.memory import people
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(people.router)
    c = TestClient(app)
    pid = c.post("/api/people", json={"name": "Chawin", "relationship": "teammate",
                                      "known_since": "2024-09-01"}).json()["id"]
    world.bridge.sync_once()
    [(_, args)] = world.fake.reducer_calls("upsert_person")
    assert args[:5] == [pid, "Chawin", "", "teammate", "2024-09-01"]
    assert args[5] > 0 and args[6] >= args[5]
    c.delete(f"/api/people/{pid}")
    world.bridge.sync_once()
    assert world.fake.reducer_calls("delete_person") == [("delete_person", [pid])]


def test_ghost_people_deleted_on_connect(world):
    p = add_person()
    world.fake.person_ids = [p, 500]
    world.bridge.sync_once()
    assert world.fake.reducer_calls("delete_person") == [("delete_person", [500])]
    world.fake.clear()
    world.bridge.sync_once()
    assert world.fake.reducer_calls("delete_person") == []


def test_people_resent_after_republish_wipe(world):
    p = add_person()
    world.bridge.sync_once()
    world.fake.remote_belongings.clear()               # republish --delete-data
    with pytest.raises(sb.BridgeLost):
        world.bridge.sync_once()
    world.fake.clear()
    world.bridge.sync_once()
    assert [a[0] for _, a in world.fake.reducer_calls("upsert_person")] == [p]


def test_person_rejected_does_not_block_others(world):
    add_person()
    add_alert()
    world.fake.reject["upsert_person"] = "person name must not be empty"
    world.bridge.sync_once()
    world.bridge.sync_once()
    assert len(world.fake.reducer_calls("upsert_alert")) == 1
    assert len(world.fake.reducer_calls("upsert_person")) == 1
    assert sum("rejected" in l for l in world.logs) == 1


def test_db_without_people_table_still_syncs(world):
    """A memory.db the bridge reads before init_db ran (old schema): no crash,
    and remote people are left alone rather than pruned."""
    with dbmod.connect() as db:
        db.execute("DROP TABLE people")
        db.commit()
    add_sighting()
    world.fake.person_ids = [3]
    counts = world.bridge.sync_once()
    assert counts["sightings"] == 1 and counts["people"] == 0
    assert world.fake.reducer_calls("delete_person") == []
