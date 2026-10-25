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
    return TestClient(server.app)


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


def test_live_frame_roundtrip(client):
    from recallai.api import server
    assert client.get("/api/live").json()["online"] is False
    assert client.get("/api/live/frame.jpg").status_code == 404
    assert client.post("/api/live", json={"frame_jpeg_b64": "!!!!"}).status_code == 400

    posted = client.post("/api/live", json={
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
