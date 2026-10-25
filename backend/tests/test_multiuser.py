"""Per-user data isolation, the first-account migration, device endpoints,
and ownership flowing from the camera to sightings, alerts and Grok."""

import base64
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from recallai import owners
from recallai.ai import agent as agentmod
from recallai.ai import watcher
from recallai.memory import db as dbmod
from recallai.memory import users
from recallai.vision import enroll
from recallai.vision.recognition import (BelongingMatcher, HistogramEmbedder,
                                         load_belongings, read_owner, set_owner)
from recallai.vision.recorder import Detection, SightingRecorder
from conftest import (BLUE, RED, PASSWORD, auth_headers, make_object_image, register,
                      sign_up)
from test_grok_features import FakeClient, fcall, message, reply

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)


def _jpeg_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.full((10, 10, 3), 120, np.uint8))


def _client(app, user=None, host="testclient"):
    c = TestClient(app, client=(host, 50000))
    if user:
        c.headers.update(auth_headers(user["token"]))
    return c


@pytest.fixture
def two(tmp_path, monkeypatch):
    """Legacy (ownerless) data, then Alice (first account: claims it) and Bob.

    Alice: My Bag (legacy) + sightings/alert/person from before accounts.
    Bob:   My Mouse, enrolled after he signed up, with his own rows."""
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "m.db")
    dbmod.init_db()
    from recallai.api import server
    monkeypatch.setattr(server, "SNAPSHOT_DIR", tmp_path / "snapshots")
    bdir = tmp_path / "belongings"
    bdir.mkdir()
    register(bdir, "My Bag", "backpack", [make_object_image(RED, seed=i) for i in range(3)],
             item_id="bag1")

    def add(**kw):
        base = dict(object_name="backpack", timestamp=(NOW - timedelta(minutes=30)).isoformat(),
                    image_path="snapshots/a_bag.jpg", crop_path="snapshots/crops/a_bag.jpg",
                    belonging_id="bag1", belonging_name="My Bag", event="left_view",
                    description="On Alice's desk", place="alice desk")
        base.update(kw)
        with dbmod.connect() as db:
            return dbmod.insert_sighting(db, **base)

    legacy_sid = add()
    generic_sid = add(belonging_id=None, belonging_name=None, image_path="snapshots/g.jpg",
                      crop_path=None, event="seen")
    with dbmod.connect() as db:
        legacy_alert = db.execute(
            "INSERT INTO alerts (belonging_id, belonging_name, sighting_id, reason, urgency,"
            " message, created_at) VALUES ('bag1','My Bag',?,'out_of_sight','low',"
            "'Alice forgot her bag', ?)", (legacy_sid, NOW.isoformat())).lastrowid
        legacy_person = db.execute(
            "INSERT INTO people (name, created_at, updated_at) VALUES ('Maya','t','t')"
        ).lastrowid
        db.commit()
    for rel in ("a_bag.jpg", "crops/a_bag.jpg", "b_mouse.jpg"):
        _jpeg_file(tmp_path / "snapshots" / rel)

    app = server.app
    anon = _client(app)
    alice = sign_up(anon, "alice")
    bob = sign_up(anon, "bob")
    a, b = _client(app, alice), _client(app, bob)

    register(bdir, "My Mouse", "mouse", [make_object_image(BLUE, seed=i) for i in range(3)],
             item_id="mouse2")
    set_owner(bdir / "mouse2", bob["user"]["id"])
    bob_sid = add(object_name="mouse", belonging_id="mouse2", belonging_name="My Mouse",
                  image_path="snapshots/b_mouse.jpg", crop_path=None,
                  description="On Bob's shelf", place="bob shelf",
                  owner_id=bob["user"]["id"])
    with dbmod.connect() as db:
        bob_alert = db.execute(
            "INSERT INTO alerts (belonging_id, belonging_name, sighting_id, reason, urgency,"
            " message, created_at, owner_id) VALUES ('mouse2','My Mouse',?,'out_of_sight',"
            "'low','Bob forgot his mouse', ?, ?)",
            (bob_sid, NOW.isoformat(), bob["user"]["id"])).lastrowid
        db.commit()
    bob_person = b.post("/api/people", json={"name": "Sam", "relationship": "roommate"}).json()["id"]
    return SimpleNamespace(
        server=server, app=app, anon=anon, a=a, b=b, alice=alice, bob=bob, dir=bdir,
        tmp=tmp_path, add=add, legacy_sid=legacy_sid, generic_sid=generic_sid,
        bob_sid=bob_sid, legacy_alert=legacy_alert, bob_alert=bob_alert,
        legacy_person=legacy_person, bob_person=bob_person,
        aid=alice["user"]["id"], bid=bob["user"]["id"])


# ================================================================ migration
def test_first_account_claims_all_ownerless_data(two):
    assert two.alice["claimed"] == {"sightings": 2, "alerts": 1, "people": 1, "belongings": 1}
    assert two.bob["claimed"] == {}            # only the very first account claims
    assert read_owner(two.dir / "bag1") == two.aid
    with dbmod.connect() as db:
        owners_by_id = dict(db.execute("SELECT id, owner_id FROM sightings").fetchall())
        assert db.execute("SELECT COUNT(*) FROM sightings WHERE owner_id IS NULL"
                          ).fetchone()[0] == 0
        assert db.execute("SELECT owner_id FROM alerts WHERE id=?",
                          (two.legacy_alert,)).fetchone()[0] == two.aid
        assert db.execute("SELECT owner_id FROM people WHERE id=?",
                          (two.legacy_person,)).fetchone()[0] == two.aid
    assert owners_by_id[two.legacy_sid] == owners_by_id[two.generic_sid] == two.aid
    assert owners_by_id[two.bob_sid] == two.bid


def test_claim_is_idempotent_and_never_steals(two):
    # Re-running the claim (e.g. the CLI) only touches still-ownerless data.
    assert users.claim_ownerless(two.bid, belongings_dir=two.dir) == {
        "sightings": 0, "alerts": 0, "people": 0, "belongings": 0}
    assert read_owner(two.dir / "bag1") == two.aid
    assert not set_owner(two.dir / "bag1", two.bid)   # owned -> not reassigned


PREVIOUS_SCHEMA = """
CREATE TABLE sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, object_name TEXT NOT NULL,
    latitude REAL, longitude REAL, accuracy REAL, timestamp TEXT NOT NULL,
    image_path TEXT NOT NULL, belonging_id TEXT, belonging_name TEXT,
    event TEXT NOT NULL DEFAULT 'seen', confidence REAL, match_score REAL, bbox TEXT,
    crop_path TEXT, nearby TEXT, verified INTEGER, verify_reason TEXT,
    description TEXT, place TEXT);
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, belonging_id TEXT NOT NULL,
    belonging_name TEXT NOT NULL, sighting_id INTEGER, reason TEXT NOT NULL,
    urgency TEXT NOT NULL DEFAULT 'low', message TEXT NOT NULL, created_at TEXT NOT NULL,
    dismissed INTEGER NOT NULL DEFAULT 0, UNIQUE (belonging_id, sighting_id));
CREATE TABLE people (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '', relationship TEXT NOT NULL DEFAULT '',
    known_since TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


def test_migration_from_pre_accounts_db_is_idempotent(tmp_path):
    p = tmp_path / "memory.db"
    c = sqlite3.connect(p)
    c.executescript(PREVIOUS_SCHEMA)
    c.execute("INSERT INTO sightings (object_name, timestamp, image_path, belonging_id)"
              " VALUES ('backpack', '2026-10-24T14:00:00+00:00', 'snapshots/1.jpg', 'bag1')")
    c.execute("INSERT INTO alerts (belonging_id, belonging_name, sighting_id, reason,"
              " message, created_at) VALUES ('bag1', 'My Bag', 1, 'out_of_sight', 'x', 't')")
    c.execute("INSERT INTO people (name, created_at, updated_at) VALUES ('Maya', 't', 't')")
    c.commit()
    c.close()

    for _ in range(3):
        dbmod.init_db(p)
    with dbmod.connect(p) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"users", "sessions"} <= tables
        for t in ("sightings", "alerts", "people"):
            cols = {r[1] for r in db.execute(f"PRAGMA table_info({t})")}
            assert "owner_id" in cols
            assert db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 1
            assert db.execute(f"SELECT owner_id FROM {t}").fetchone()[0] is None

    user, claimed = users.create_user("kent", PASSWORD, db_path=p, belongings_dir=tmp_path / "b")
    assert claimed == {"sightings": 1, "alerts": 1, "people": 1, "belongings": 0}
    dbmod.init_db(p)   # running the migration again changes nothing
    with dbmod.connect(p) as db:
        for t in ("sightings", "alerts", "people"):
            assert db.execute(f"SELECT owner_id FROM {t}").fetchone()[0] == user["id"]
    assert users.count_users(p) == 1


# ================================================================ isolation
def test_belongings_sightings_and_search_are_private(two):
    assert [i["id"] for i in two.a.get("/api/belongings").json()] == ["bag1"]
    assert [i["id"] for i in two.b.get("/api/belongings").json()] == ["mouse2"]

    assert {r["id"] for r in two.a.get("/api/sightings", params={"q": "my"}).json()} \
        == {two.legacy_sid}
    assert two.a.get("/api/sightings", params={"q": "mouse"}).json() == []
    assert two.a.get("/api/sightings", params={"belonging_id": "mouse2"}).json() == []
    assert two.b.get("/api/sightings", params={"q": "backpack"}).json() == []
    assert two.a.get("/api/sightings/latest", params={"q": "mouse"}).status_code == 404
    assert [r["id"] for r in two.b.get("/api/sightings/recent").json()] == [two.bob_sid]
    assert {o["object_name"] for o in two.b.get("/api/objects").json()} == {"mouse"}
    assert two.a.get("/api/belongings/mouse2/last-seen").status_code == 404
    assert two.b.get("/api/belongings/mouse2/last-seen").json()["sighting_id"] == two.bob_sid


def test_alerts_are_private_and_only_own_can_be_dismissed(two):
    assert [a["id"] for a in two.a.get("/api/alerts").json()] == [two.legacy_alert]
    assert [a["id"] for a in two.b.get("/api/alerts").json()] == [two.bob_alert]
    # Alice can't dismiss Bob's alert (indistinguishable from a missing id)
    assert two.a.post(f"/api/alerts/{two.bob_alert}/dismiss").status_code == 404
    with dbmod.connect() as db:
        assert db.execute("SELECT dismissed FROM alerts WHERE id=?",
                          (two.bob_alert,)).fetchone()[0] == 0
    assert two.b.post(f"/api/alerts/{two.bob_alert}/dismiss").status_code == 200
    assert two.b.get("/api/alerts").json() == []
    assert len(two.b.get("/api/alerts", params={"include_dismissed": True}).json()) == 1
    assert len(two.a.get("/api/alerts").json()) == 1


def test_people_are_private(two):
    assert [p["name"] for p in two.a.get("/api/people").json()] == ["Maya"]
    assert [p["name"] for p in two.b.get("/api/people").json()] == ["Sam"]
    assert two.a.put(f"/api/people/{two.bob_person}", json={"name": "Hacked"}).status_code == 404
    assert two.a.delete(f"/api/people/{two.bob_person}").status_code == 404
    assert [p["name"] for p in two.b.get("/api/people").json()] == ["Sam"]


def test_snapshots_only_for_their_owner(two):
    token_a, token_b = two.alice["token"], two.bob["token"]
    assert two.a.get("/snapshots/a_bag.jpg").status_code == 200        # Bearer
    r = two.anon.get(f"/snapshots/crops/a_bag.jpg?token={token_a}")   # <img> style
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert two.anon.get(f"/snapshots/a_bag.jpg?token={token_b}").status_code == 404
    assert two.b.get("/snapshots/b_mouse.jpg").status_code == 200
    assert two.a.get("/snapshots/b_mouse.jpg").status_code == 404
    assert two.anon.get("/snapshots/a_bag.jpg").status_code == 401
    # files no sighting points at, and path tricks, are not served
    _jpeg_file(two.tmp / "snapshots" / "stray.jpg")
    assert two.a.get("/snapshots/stray.jpg").status_code == 404
    assert two.a.get("/snapshots/../m.db").status_code == 404
    assert two.a.get("/snapshots/%2e%2e/m.db").status_code == 404


# ================================================================ GPS
def test_gps_is_per_user_and_device_sees_each_owner(two):
    assert two.a.post("/gps", json={"latitude": 1, "longitude": 2, "accuracy": 3}).status_code == 200
    assert two.a.get("/gps/latest").json()["latitude"] == 1
    assert two.b.get("/gps/latest").json() == {"status": "No GPS data"}
    camera = _client(two.app, host="127.0.0.1")
    by_owner = camera.get("/api/device/gps").json()["by_owner"]
    assert set(by_owner) == {str(two.aid)} and by_owner[str(two.aid)]["longitude"] == 2
    assert two.server.fresh_gps(two.bid) is None
    # a signed-in browser is not the device
    assert two.a.get("/api/device/gps").status_code == 403
    # the dashboard posts the same fix under /api (its dev proxy only forwards /api)
    assert two.b.post("/api/gps", json={"latitude": 5, "longitude": 6, "accuracy": 7}).status_code == 200
    assert two.b.get("/gps/latest").json()["latitude"] == 5


# ================================================================ devices
def _frame_b64():
    ok, buf = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))
    return base64.b64encode(buf.tobytes()).decode()


@pytest.mark.parametrize("host,headers,ok", [
    ("127.0.0.1", {}, True),
    ("::1", {}, True),
    ("testclient", {}, False),                                   # not loopback
    ("192.168.1.20", {}, False),                                 # LAN
    ("127.0.0.1", {"Origin": "http://localhost:5173"}, False),   # a browser page
    ("127.0.0.1", {"X-Forwarded-For": "192.168.1.20"}, False),   # via a proxy
])
def test_device_endpoint_accepts_only_local_camera(two, host, headers, ok):
    r = _client(two.app, host=host).post("/api/live", json={"frame_jpeg_b64": _frame_b64()},
                                         headers=headers)
    assert r.status_code == (200 if ok else 403)


def test_device_key_lets_a_remote_camera_in(two, monkeypatch):
    monkeypatch.setenv("RECALLAI_DEVICE_KEY", "s3cret-key")
    remote = _client(two.app, host="192.168.1.20")
    body = {"frame_jpeg_b64": _frame_b64()}
    assert remote.post("/api/live", json=body).status_code == 403
    assert remote.post("/api/live", json=body,
                       headers={"X-RecallAI-Device-Key": "wrong"}).status_code == 403
    assert remote.post("/api/live", json=body,
                       headers={"X-RecallAI-Device-Key": "s3cret-key"}).status_code == 200
    assert remote.get("/api/device/gps",
                      headers={"X-RecallAI-Device-Key": "s3cret-key"}).status_code == 200
    # A signed-in user's token is not a device credential.
    assert _client(two.app, two.alice, host="192.168.1.20").post(
        "/api/live", json=body).status_code == 403


def test_live_status_lists_only_my_items_in_view(two):
    camera = _client(two.app, host="127.0.0.1")
    camera.post("/api/live", json={"frame_jpeg_b64": _frame_b64(), "in_view": [
        {"belonging_id": "bag1", "name": "My Bag", "bbox": [1, 2, 3, 4]},
        {"belonging_id": "mouse2", "name": "My Mouse", "bbox": [5, 6, 7, 8]},
    ], "detections": [{"category": "backpack"}, {"category": "mouse"}]})
    a, b = two.a.get("/api/live").json(), two.b.get("/api/live").json()
    assert [i["name"] for i in a["in_view"]] == ["My Bag"]
    assert [i["name"] for i in b["in_view"]] == ["My Mouse"]
    assert len(a["detections"]) == len(b["detections"]) == 2   # generic YOLO boxes
    # the frame itself is fine for any signed-in user
    assert two.b.get("/api/live/frame.jpg").status_code == 200


# ================================================================ enrollment
def test_enrollment_is_owned_by_whoever_pressed_add(two):
    camera = _client(two.app, host="127.0.0.1")
    started = two.b.post("/api/enroll/start").json()
    assert started["active"] and started["mine"] and "owner_id" not in started
    # The camera is told who owns the session ...
    assert camera.post("/api/live", json={}).json()["enroll"]["owner_id"] == two.bid
    # ... and Alice only sees that the camera is busy.
    busy = two.a.get("/api/enroll").json()
    assert busy["phase"] == "busy" and busy["mine"] is False and busy["name"] is None
    assert two.a.post("/api/enroll/start").status_code == 409
    assert two.a.post("/api/enroll/cancel").status_code == 409

    # An older camera that doesn't write owner_id: the server tags the item.
    info, _ = enroll.save_angle(two.dir, "cup", make_object_image(RED, seed=3))
    assert read_owner(two.dir / info["id"]) is None
    camera.post("/api/live", json={"enroll": {"phase": "turn", "saved": 1,
                                              "category": "cup", "belonging_id": info["id"]}})
    assert read_owner(two.dir / info["id"]) == two.bid
    assert two.b.post("/api/enroll/cancel").json()["active"] is False
    assert two.a.post("/api/enroll/start").json()["mine"] is True


def test_save_angle_writes_owner_and_keeps_owners_apart(tmp_path):
    img = make_object_image(RED, seed=1)
    a1, _ = enroll.save_angle(tmp_path, "cup", img, owner_id=1)
    b1, _ = enroll.save_angle(tmp_path, "cup", img, owner_id=2)
    assert a1["id"] != b1["id"]                     # unfinished items are per owner
    assert a1["name"] == b1["name"] == "cup"        # names are de-duplicated per owner
    assert a1["owner_id"] == 1 and b1["owner_id"] == 2
    # continuing someone else's belonging id starts your own item instead
    again, n = enroll.save_angle(tmp_path, "cup", img, belonging_id=a1["id"], owner_id=2)
    assert again["id"] == b1["id"] and n == 2
    a2, _ = enroll.save_angle(tmp_path, "cup", img, owner_id=1)
    assert a2["id"] == a1["id"]
    assert {b.owner_id for b in load_belongings(tmp_path)} == {1, 2}


# ================================================================ camera -> sightings
def test_recorder_tags_each_sighting_with_its_owner_and_their_gps(tmp_path, belongings_dir):
    register(belongings_dir, "Alice Bag", "backpack",
             [make_object_image(RED, seed=i) for i in range(5)], item_id="abag")
    register(belongings_dir, "Bob Bag", "backpack",
             [make_object_image(BLUE, seed=i) for i in range(5)], item_id="bbag")
    set_owner(belongings_dir / "abag", 1)
    set_owner(belongings_dir / "bbag", 2)
    rec = SightingRecorder(BelongingMatcher(HistogramEmbedder(), belongings_dir),
                           db_path=tmp_path / "x.db", snapshot_dir=tmp_path / "s")
    frame = np.full((480, 640, 3), 90, np.uint8)
    frame[150:270, 40:200] = make_object_image(RED, seed=50)
    frame[150:270, 300:460] = make_object_image(BLUE, seed=51)
    gps = {"by_owner": {"1": {"latitude": 10.0, "longitude": 20.0, "accuracy": 5.0}}}
    found = rec.process(frame, [Detection("backpack", 0.9, (40, 150, 200, 270)),
                                Detection("backpack", 0.9, (300, 150, 460, 270))],
                        gps=gps, now=0)
    assert set(found) == {"abag", "bbag"}
    rows = {r["belonging_id"]: r for r in rec.db.execute("SELECT * FROM sightings")}
    rec.close()
    assert rows["abag"]["owner_id"] == 1 and rows["abag"]["latitude"] == 10.0
    assert rows["bbag"]["owner_id"] == 2 and rows["bbag"]["latitude"] is None   # Bob: no GPS


def test_recorder_picks_up_an_owner_claimed_after_start(tmp_path, belongings_dir):
    register(belongings_dir, "My Bag", "backpack",
             [make_object_image(RED, seed=i) for i in range(5)], item_id="bag1")
    rec = SightingRecorder(BelongingMatcher(HistogramEmbedder(), belongings_dir),
                           db_path=tmp_path / "x.db", snapshot_dir=tmp_path / "s",
                           save_interval=1)
    frame = np.full((480, 640, 3), 90, np.uint8)
    frame[150:270, 40:200] = make_object_image(RED, seed=50)
    det = [Detection("backpack", 0.9, (40, 150, 200, 270))]
    rec.process(frame, det, now=0)
    set_owner(belongings_dir / "bag1", 7)      # first account signs up meanwhile
    rec.process(frame, det, now=10)            # past the owner cache TTL
    owners_seen = [r[0] for r in rec.db.execute("SELECT owner_id FROM sightings ORDER BY id")]
    rec.close()
    assert owners_seen == [None, 7]


# ================================================================ watcher
def test_watcher_alerts_carry_owner_and_use_that_owners_gps(two):
    two.add(object_name="mouse", belonging_id="mouse2", belonging_name="My Mouse",
            timestamp=(NOW - timedelta(minutes=1)).isoformat(), event="seen",
            latitude=37.80, longitude=-122.45, accuracy=10, owner_id=two.bid)
    two.add(timestamp=(NOW - timedelta(minutes=1)).isoformat(), event="seen",
            latitude=37.80, longitude=-122.45, accuracy=10, owner_id=two.aid)
    far = {"latitude": 37.90, "longitude": -122.45, "accuracy": 10}
    near = {"latitude": 37.80, "longitude": -122.45, "accuracy": 10}
    phones = {two.aid: near, two.bid: far}
    client = FakeClient(lambda kw: reply(fcall("decide_alerts", {"alerts": [
        {"belonging_id": c, "send": True, "urgency": "high", "message": f"msg {c}"}
        for c in ("bag1", "mouse2") if c in json.dumps(kw)]})))
    new = watcher.run_once(client=client, gps=phones.get, now=NOW, belongings_dir=two.dir)
    assert [(n["belonging"], n["owner_id"]) for n in new] == [("My Mouse", two.bid)]
    # one Grok review per owner, and Bob's prompt never mentions Alice's bag
    prompts = [json.dumps(c) for c in client.calls]
    assert len(prompts) == 1 and "mouse2" in prompts[0] and "bag1" not in prompts[0]
    assert sorted(a["message"] for a in two.b.get("/api/alerts").json()) == [
        "Bob forgot his mouse", "msg mouse2"]
    assert "msg mouse2" not in json.dumps(two.a.get("/api/alerts").json())


def test_find_candidates_can_be_scoped(two):
    two.add(object_name="mouse", belonging_id="mouse2", belonging_name="My Mouse",
            owner_id=two.bid)                     # a new left_view, not alerted yet
    two.add(owner_id=two.aid)
    later = NOW + timedelta(hours=1)
    everyone = watcher.find_candidates(now=later, belongings_dir=two.dir)
    only_bob = watcher.find_candidates(now=later, belongings_dir=two.dir, owner_id=two.bid)
    assert {c["belonging"].id for c in everyone} == {"bag1", "mouse2"}
    assert [c["belonging"].id for c in only_bob] == ["mouse2"]


# ================================================================ Grok agent
def test_agent_tools_are_scoped_to_the_requesting_user(two, monkeypatch):
    """Grok asks for everything it can think of, including Bob's ids; Alice's
    request only ever returns Alice's data."""
    script = [
        reply(fcall("list_belongings", {}, "c1"),
              fcall("search_sightings", {}, "c2"),
              fcall("search_sightings", {"text": "shelf"}, "c3"),
              fcall("get_last_seen", {"belonging": "mouse"}, "c4"),
              fcall("get_last_seen", {"belonging": "mouse2"}, "c5"),
              fcall("look_at_photo", {"sighting_id": two.bob_sid, "question": "?"}, "c6"),
              fcall("list_people", {}, "c7"),
              fcall("list_belongings", {"not_seen_for_minutes": 1}, "c8")),
        reply(message(f"Bag: [#{two.legacy_sid}]. Mouse: [#{two.bob_sid}].")),
    ]
    client = FakeClient(lambda kw: script.pop(0) if "tools" in kw
                        else reply(message("vision")))
    monkeypatch.setattr(two.server.agent, "client", client)
    monkeypatch.setattr(two.server.agent.tools, "belongings_dir", two.dir)
    monkeypatch.setattr(two.server.agent.tools, "now", NOW)

    r = two.a.post("/api/ask", json={"question": "where is everything?"})
    assert r.status_code == 200
    out = r.json()
    # cited sightings: Bob's id is dropped even though the model cited it
    assert [s["id"] for s in out["sightings"]] == [two.legacy_sid]
    outputs = {o["call_id"]: o["output"] for o in client.calls[1]["input"]}
    everything = json.dumps(outputs)
    # (c5 echoes the model's own "mouse2" query back; nothing of Bob's leaks)
    for secret in ("My Mouse", "Bob's shelf", "Sam", "bob shelf", "b_mouse"):
        assert secret not in everything, secret
    assert "My Bag" in outputs["c1"] and "Maya" in outputs["c7"]
    assert outputs["c3"] == "No matching sightings."
    assert outputs["c4"].startswith("No registered belonging") \
        and outputs["c5"].startswith("No registered belonging")
    assert outputs["c6"] == f"No sighting #{two.bob_sid}"
    # the owner is not something the model can pass
    for tool in client.calls[0]["tools"]:
        assert "owner" not in json.dumps(tool["parameters"])

    # Bob, same shared agent: sees only his.
    script[:] = [reply(fcall("list_belongings", {}, "d1")),
                 reply(message(f"[#{two.bob_sid}] [#{two.legacy_sid}]"))]
    client.calls.clear()
    out = two.b.post("/api/ask", json={"question": "and mine?"}).json()
    assert [s["id"] for s in out["sightings"]] == [two.bob_sid]
    assert "My Bag" not in client.calls[1]["input"][0]["output"]


def test_agent_gps_is_the_askers_phone(two, monkeypatch):
    two.a.post("/gps", json={"latitude": 5, "longitude": 6, "accuracy": 7})
    script = [reply(fcall("get_context", {}, "g")), reply(message("ok"))]
    client = FakeClient(lambda kw: script.pop(0))
    monkeypatch.setattr(two.server.agent, "client", client)
    two.b.post("/api/ask", json={"question": "where am I?"})
    assert '"phone_gps": "not connected"' in client.calls[1]["input"][0]["output"]
    script[:] = [reply(fcall("get_context", {}, "g")), reply(message("ok"))]
    client.calls.clear()
    two.a.post("/api/ask", json={"question": "where am I?"})
    assert '"latitude": 5' in client.calls[1]["input"][0]["output"]


def test_memory_tools_owner_any_is_the_unscoped_cli_default(two):
    tools = agentmod.MemoryTools(belongings_dir=two.dir, now=NOW)
    assert tools.owner_id is owners.ANY
    names = {b["name"] for b in json.loads(tools.tool_list_belongings())}
    assert names == {"My Bag", "My Mouse"}
    scoped = tools.for_request(owner_id=two.bid)
    assert {b["name"] for b in json.loads(scoped.tool_list_belongings())} == {"My Mouse"}
    assert tools.owner_id is owners.ANY          # the shared instance is unchanged


# ================================================================ CLI owner
def test_register_cli_owner_resolution(tmp_path, monkeypatch):
    p = tmp_path / "m.db"
    dbmod.init_db(p)
    assert users.resolve_cli_owner(db_path=p) is None             # no accounts yet
    kent, _ = users.create_user("kent", PASSWORD, db_path=p, belongings_dir=tmp_path)
    assert users.resolve_cli_owner(db_path=p) == kent["id"]       # the only account
    maya, _ = users.create_user("maya", PASSWORD, db_path=p, belongings_dir=tmp_path)
    with pytest.raises(SystemExit, match="--user"):
        users.resolve_cli_owner(db_path=p)
    assert users.resolve_cli_owner("MAYA", db_path=p) == maya["id"]
    with pytest.raises(SystemExit, match="No account"):
        users.resolve_cli_owner("ghost", db_path=p)


def test_register_create_item_writes_owner(tmp_path, monkeypatch):
    from recallai.vision import register as reg
    monkeypatch.setattr(reg, "BELONGINGS_DIR", tmp_path)
    folder, info = reg.create_item("My Keys", "remote", owner_id=3)
    assert info["owner_id"] == 3 and read_owner(folder) == 3
    folder, info = reg.create_item("Old Keys", "remote")
    assert "owner_id" not in info and read_owner(folder) is None


def test_users_cli_list_and_claim(tmp_path, monkeypatch, capsys):
    from recallai.vision import recognition
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "m.db")
    # the CLI claims belongings in the default folder: point it at a tmp one
    monkeypatch.setattr(recognition, "BELONGINGS_DIR", tmp_path / "belongings")
    register(tmp_path / "belongings", "Old Keys", "remote", [], item_id="keys")
    dbmod.init_db()
    assert users.main(["list"]) == 0
    assert "No accounts yet" in capsys.readouterr().out
    users.create_user("kent", PASSWORD, belongings_dir=tmp_path / "none")
    with dbmod.connect() as db:
        dbmod.insert_sighting(db, object_name="cup", timestamp="t", image_path="x.jpg")
    assert users.main(["claim", "kent"]) == 0
    assert "sightings=1, alerts=0, people=0, belongings=1" in capsys.readouterr().out
    assert read_owner(tmp_path / "belongings" / "keys") is not None
    assert users.main(["claim", "ghost"]) == 1


# ================================================================ deleting belongings
def test_delete_belonging_removes_photos_and_history_and_tells_the_camera(two):
    camera = _client(two.app, host="127.0.0.1")
    before = camera.post("/api/live", json={}).json()["belongings_version"]
    # Bob can't delete Alice's bag; it isn't his.
    assert two.b.delete("/api/belongings/bag1").status_code == 404
    assert (two.dir / "bag1").is_dir()

    r = two.a.delete("/api/belongings/bag1")
    assert r.status_code == 200
    assert r.json() == {"status": "deleted", "id": "bag1", "name": "My Bag",
                        "sightings": 1, "alerts": 1}
    assert not (two.dir / "bag1").exists()
    assert [b["id"] for b in two.a.get("/api/belongings").json()] == []
    with dbmod.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sightings WHERE belonging_id='bag1'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM alerts WHERE belonging_id='bag1'").fetchone()[0] == 0
        # Generic sightings and Bob's things are untouched.
        assert db.execute("SELECT COUNT(*) FROM sightings WHERE id IN (?, ?)",
                          (two.generic_sid, two.bob_sid)).fetchone()[0] == 2
    assert (two.dir / "mouse2").is_dir()
    assert camera.post("/api/live", json={}).json()["belongings_version"] == before + 1
    assert two.a.delete("/api/belongings/bag1").status_code == 404


def test_rename_belonging_updates_history_keeps_alias_and_tells_the_camera(two):
    camera = _client(two.app, host="127.0.0.1")
    before = camera.post("/api/live", json={}).json()["belongings_version"]
    assert two.b.put("/api/belongings/bag1", json={"name": "Not yours"}).status_code == 404
    assert two.a.put("/api/belongings/bag1", json={"name": "   "}).status_code == 422

    r = two.a.put("/api/belongings/bag1", json={"name": "  Alice's   blue backpack "})
    assert r.status_code == 200
    assert r.json() == {"id": "bag1", "name": "Alice's blue backpack", "previous": "My Bag"}
    info = json.loads((two.dir / "bag1" / "info.json").read_text())
    assert info["name"] == "Alice's blue backpack" and info["aliases"] == ["My Bag"]
    with dbmod.connect() as db:
        names = {n for (n,) in db.execute(
            "SELECT belonging_name FROM sightings WHERE belonging_id='bag1'")}
        alert = db.execute("SELECT belonging_name FROM alerts WHERE id=?",
                           (two.legacy_alert,)).fetchone()[0]
    assert names == {"Alice's blue backpack"} and alert == "Alice's blue backpack"
    assert camera.post("/api/live", json={}).json()["belongings_version"] == before + 1

    # Renaming back drops it from the aliases instead of listing it twice.
    two.a.put("/api/belongings/bag1", json={"name": "my bag"})
    info = json.loads((two.dir / "bag1" / "info.json").read_text())
    assert info["name"] == "my bag" and info["aliases"] == ["Alice's blue backpack"]
