"""People directory, live camera mirror, and the agent's list_people tool."""

import base64
import json
import time
from datetime import date, timedelta

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from recallai.memory import db as dbmod
from recallai.ai.agent import MemoryTools
from recallai.memory.people import known_for
from recallai.vision.recognition import set_owner
from conftest import register, sign_in


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "m.db")
    dbmod.init_db()
    from recallai.api import server
    with server._live_lock:
        server._live["jpeg"] = None
        server._live["updated_at"] = 0.0
        server._live["meta"] = {
            "gps": False, "width": None, "height": None,
            "in_view": [], "detections": [],
        }
    c = TestClient(server.app)
    c.user = sign_in(c)["user"]
    return c


def test_known_for_phrases():
    today = date(2026, 10, 24)
    assert known_for("2024-08-24", today) == "2 years and 2 months"
    assert known_for("2024-10-30", today) == "1 year and 11 months"
    assert known_for("2026-01-24", today) == "9 months"
    assert known_for("2026-10-20", today) == "4 days"
    assert known_for("2026-10-23", today) == "1 day"
    assert known_for("2026-10-24", today) == "since today"
    assert known_for(None, today) is None
    assert known_for("not-a-date", today) is None


def test_people_crud(client):
    assert client.get("/api/people").json() == []

    created = client.post("/api/people", json={
        "name": "  Maya Chen ",
        "description": "Lab partner. Red jacket, sits by the window.",
        "relationship": "friend",
        "known_since": "2024-08-01",
    })
    assert created.status_code == 201
    person = created.json()
    assert person["name"] == "Maya Chen"
    assert person["relationship"] == "friend"
    assert person["known_since"] == "2024-08-01"
    assert "year" in person["known_for"]

    listed = client.get("/api/people").json()
    assert [p["name"] for p in listed] == ["Maya Chen"]

    edited = client.put(f"/api/people/{person['id']}", json={
        "name": "Maya Chen",
        "description": "Friend from the lab.",
        "relationship": "classmate",
        "known_since": "",
    })
    assert edited.status_code == 200
    assert edited.json()["relationship"] == "classmate"
    assert edited.json()["known_since"] is None
    assert edited.json()["known_for"] is None

    assert client.delete(f"/api/people/{person['id']}").json()["status"] == "deleted"
    assert client.get("/api/people").json() == []
    assert client.delete(f"/api/people/{person['id']}").status_code == 404
    assert client.put(f"/api/people/{person['id']}", json={"name": "x"}).status_code == 404


def test_people_rejects_bad_dates(client):
    assert client.post("/api/people", json={"name": "   "}).status_code == 422
    future = (date.today() + timedelta(days=2)).isoformat()
    assert client.post("/api/people", json={
        "name": "Future", "known_since": future,
    }).status_code == 422
    assert client.post("/api/people", json={
        "name": "Nope", "known_since": "October",
    }).status_code == 422


def test_agent_lists_people(client):
    assert MemoryTools().tool_list_people() == "No people saved yet."
    met = (date.today() - timedelta(days=400)).isoformat()
    client.post("/api/people", json={
        "name": "Sam",
        "description": "Roommate",
        "relationship": "roommate",
        "known_since": met,
    })
    rows = json.loads(MemoryTools().tool_list_people())
    assert rows[0]["name"] == "Sam"
    assert rows[0]["relationship"] == "roommate"
    assert "year" in rows[0]["known_for"]


def _jpeg_b64():
    ok, buf = cv2.imencode(".jpg", np.full((12, 18, 3), (40, 80, 120), np.uint8))
    assert ok
    return base64.b64encode(buf.tobytes()).decode()


def test_live_frame_roundtrip(client, tmp_path):
    from recallai.api import server
    # The signed-in user's own belonging, so it is listed in in_view.
    folder = tmp_path / "belongings"
    register(folder, "My Bag", "backpack", [], item_id="bag1")
    set_owner(folder / "bag1", client.user["id"])
    # The camera process: a local device (loopback, no browser Origin).
    camera = TestClient(server.app, client=("127.0.0.1", 50000))
    assert client.get("/api/live").json()["online"] is False
    assert client.get("/api/live/frame.jpg").status_code == 404
    assert camera.post("/api/live", json={"frame_jpeg_b64": "!!!!"}).status_code == 400

    posted = camera.post("/api/live", json={
        "frame_jpeg_b64": _jpeg_b64(),
        "gps": True,
        "width": 18,
        "height": 12,
        "in_view": [{
            "belonging_id": "bag1", "name": "My Bag", "category": "backpack",
            "score": 0.91, "bbox": [1, 2, 10, 11],
        }],
        "detections": [{"category": "bottle", "confidence": 0.62, "bbox": [0, 0, 4, 5]}],
    })
    assert posted.status_code == 200
    live = client.get("/api/live").json()
    assert live["online"] is True
    assert live["gps"] is True
    assert live["in_view"][0]["name"] == "My Bag"
    assert live["detections"][0]["category"] == "bottle"
    frame = client.get("/api/live/frame.jpg")
    assert frame.status_code == 200
    assert frame.headers["content-type"].startswith("image/jpeg")
    assert frame.content.startswith(b"\xff\xd8")

    with server._live_lock:
        server._live["updated_at"] = time.monotonic() - 30
    assert client.get("/api/live").json()["online"] is False
    assert client.get("/api/live/frame.jpg").status_code == 404


def test_publisher_posts_scaled_jpeg(monkeypatch):
    from recallai.vision import live_feed
    captured = []

    def fake_post(url, json=None, timeout=None):
        captured.append((url, json))
        return type("R", (), {"status_code": 200})()

    monkeypatch.setattr(live_feed.requests, "post", fake_post)
    pub = live_feed.LivePublisher("http://example.test/api/live", interval=0.05)
    try:
        frame = np.zeros((1000, 2000, 3), np.uint8)
        pub.submit(frame, {
            "gps": False,
            "in_view": [{"name": "My Bag", "bbox": [0, 0, 2000, 1000]}],
            "detections": [{"category": "cup", "confidence": 0.4, "bbox": [10, 10, 30, 40]}],
        })
        for _ in range(40):
            if captured:
                break
            time.sleep(0.05)
    finally:
        pub.close()

    assert captured
    url, body = captured[0]
    assert url == "http://example.test/api/live"
    assert body["width"] == 960
    assert body["height"] == 480
    assert body["in_view"][0]["bbox"][2] == 960
    jpeg = base64.b64decode(body["frame_jpeg_b64"])
    assert jpeg.startswith(b"\xff\xd8")


PERSON_BOX = [104, 44, 204, 234]


def _push(camera, **extra):
    """One camera frame with a person and a chair in it; returns the reply."""
    return camera.post("/api/live", json={
        "frame_jpeg_b64": _jpeg_b64(), "width": 480, "height": 270,
        "detections": [
            {"category": "person", "confidence": 0.9, "bbox": PERSON_BOX},
            {"category": "chair", "confidence": 0.7, "bbox": [300, 100, 400, 260]},
        ],
        **extra,
    }).json()


def test_name_a_person_on_the_live_feed(client):
    from recallai.api import server
    camera = TestClient(server.app, client=("127.0.0.1", 50000))
    click = {"bbox": [100, 40, 200, 230]}       # a beat behind the camera's box
    off = client.post("/api/live/people", json={**click, "name": "Maya"})
    assert off.status_code == 409 and off.json()["detail"] == "The camera is off."

    _push(camera)
    chair = client.post("/api/live/people", json={"bbox": [300, 100, 400, 260], "name": "Maya"})
    assert chair.status_code == 409 and "isn't in view" in chair.json()["detail"]
    assert client.post("/api/live/people", json={**click, "name": "  "}).status_code == 422
    assert client.get("/api/people").json() == []

    named = client.post("/api/live/people", json={**click, "name": " Maya "})
    assert named.status_code == 200
    body = named.json()
    assert body["created"] is True and body["person"]["name"] == "Maya"
    assert body["bbox"] == PERSON_BOX
    pid = body["person"]["id"]
    assert [p["name"] for p in client.get("/api/people").json()] == ["Maya"]

    again = client.post("/api/live/people", json={**click, "name": "maya"}).json()
    assert again["created"] is False and again["person"]["id"] == pid
    by_id = client.post("/api/live/people", json={**click, "person_id": pid}).json()
    assert by_id["person"]["id"] == pid

    # The camera gets each job once, with its next frame.
    job = {"person_id": pid, "bbox": PERSON_BOX, "width": 480}
    assert _push(camera)["people_jobs"] == [job] * 3
    assert _push(camera)["people_jobs"] == []

    # The camera recognizes Maya; a rename shows without waiting for it.
    _push(camera, people=[
        {"person_id": pid, "name": "Maya", "score": 0.83, "bbox": PERSON_BOX},
        {"person_id": pid + 100, "name": "Not yours", "score": 0.9, "bbox": [0, 0, 9, 9]},
    ])
    client.put(f"/api/people/{pid}", json={"name": "Maya Chen"})
    assert client.get("/api/live").json()["people"] == [
        {"person_id": pid, "name": "Maya Chen", "score": 0.83, "bbox": PERSON_BOX}]


def test_live_people_stay_in_their_account(client, tmp_path):
    from recallai import paths
    from recallai.api import server
    camera = TestClient(server.app, client=("127.0.0.1", 50000))
    other = TestClient(server.app)
    sign_in(other, "lois")
    _push(camera)
    mom = client.post("/api/live/people", json={"bbox": PERSON_BOX, "name": "Mom"}).json()["person"]

    stolen = other.post("/api/live/people", json={"bbox": PERSON_BOX, "person_id": mom["id"]})
    assert stolen.status_code == 404
    # Same name in another account is another person.
    theirs = other.post("/api/live/people", json={"bbox": PERSON_BOX, "name": "Mom"}).json()
    assert theirs["created"] is True and theirs["person"]["id"] != mom["id"]

    _push(camera, people=[{"person_id": mom["id"], "name": "Mom", "score": 0.8, "bbox": PERSON_BOX}])
    assert [p["person_id"] for p in client.get("/api/live").json()["people"]] == [mom["id"]]
    assert other.get("/api/live").json()["people"] == []

    # Deleting a person deletes the camera's photos of them.
    folder = paths.PEOPLE_DIR / str(mom["id"])
    folder.mkdir(parents=True)
    (folder / "reference_1.jpg").write_bytes(b"x")
    assert client.delete(f"/api/people/{mom['id']}").status_code == 200
    assert not folder.exists()


def test_publisher_hands_over_people_jobs(monkeypatch):
    from recallai.vision import live_feed
    sent = []
    job = {"person_id": 4, "bbox": [1, 2, 30, 40], "width": 960}

    def fake_post(url, json=None, timeout=None):
        sent.append(json)
        return type("R", (), {"json": lambda self: {"enroll": {}, "people_jobs": [job]}})()

    monkeypatch.setattr(live_feed.requests, "post", fake_post)
    pub = live_feed.LivePublisher("http://example.test/api/live", interval=0.02)
    try:
        pub.submit(np.zeros((1000, 2000, 3), np.uint8), {
            "people": [{"person_id": 4, "name": "Maya", "score": 0.8, "bbox": [0, 0, 2000, 1000]}],
        })
        for _ in range(50):
            if sent:
                break
            time.sleep(0.02)
        time.sleep(0.05)
    finally:
        pub.close()
    assert sent[0]["people"][0]["bbox"] == [0, 0, 960, 480]
    assert pub.take_people_jobs() == [job]
    assert pub.take_people_jobs() == []
