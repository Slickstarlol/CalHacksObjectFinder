"""Search API + server endpoints against a temporary database."""

import pytest
from fastapi.testclient import TestClient

import db as dbmod
from conftest import RED, make_object_image, register


@pytest.fixture
def client(tmp_path, belongings_dir, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "m.db")
    import search
    import server
    monkeypatch.setattr(search, "BELONGINGS_DIR", belongings_dir)
    dbmod.init_db()

    register(belongings_dir, "My Bag", "backpack",
             [make_object_image(RED, seed=i) for i in range(3)], item_id="bag1")
    register(belongings_dir, "My Mouse", "mouse", [], item_id="mouse1")

    with dbmod.connect() as db:
        # legacy generic sighting (from the old webcam.py)
        dbmod.insert_sighting(db, object_name="backpack", latitude=37.8,
                              longitude=-122.4, accuracy=15,
                              timestamp="2026-10-24T14:14:14+00:00",
                              image_path="snapshots/old.jpg")
        dbmod.insert_sighting(db, object_name="backpack", timestamp="2026-10-24T15:00:00+00:00",
                              image_path="snapshots/a.jpg", belonging_id="bag1",
                              belonging_name="My Bag", event="seen",
                              crop_path="snapshots/crops/a.jpg", nearby="bottle, laptop",
                              match_score=0.91)
        dbmod.insert_sighting(db, object_name="backpack", timestamp="2026-10-24T15:05:00+00:00",
                              image_path="snapshots/b.jpg", belonging_id="bag1",
                              belonging_name="My Bag", event="left_view",
                              crop_path="snapshots/crops/b.jpg", nearby="chair")
    return TestClient(server.app)


def test_belongings_list(client):
    items = client.get("/api/belongings").json()
    by_name = {i["name"]: i for i in items}
    assert by_name["My Bag"]["sightings"] == 2
    assert by_name["My Bag"]["last"]["event"] == "left_view"
    assert by_name["My Bag"]["last"]["nearby"] == ["chair"]
    assert by_name["My Mouse"]["photos"] == 0 and by_name["My Mouse"]["last"] is None
    assert items[0]["name"] == "My Bag"  # most recently seen first


@pytest.mark.parametrize("q", ["my bag", "MY BAG", "bag"])
def test_search_by_belonging_name(client, q):
    rows = client.get("/api/sightings", params={"q": q}).json()
    assert rows[0]["belonging_name"] == "My Bag"
    assert rows[0]["event"] == "left_view"  # newest first


def test_search_by_category_puts_mine_first(client):
    rows = client.get("/api/sightings", params={"q": "backpack"}).json()
    assert [r["belonging_id"] for r in rows] == ["bag1", "bag1", None]
    assert rows[-1]["maps_url"] and rows[0]["maps_url"] is None


def test_search_by_id(client):
    rows = client.get("/api/sightings", params={"belonging_id": "bag1"}).json()
    assert len(rows) == 2


def test_latest_and_not_found(client):
    assert client.get("/api/sightings/latest", params={"q": "my bag"}).json()["event"] == "left_view"
    assert client.get("/api/sightings/latest", params={"q": "umbrella"}).status_code == 404
    assert client.get("/api/sightings").status_code == 422


def test_pages_and_gps(client):
    assert "RecallAI" in client.get("/search").text
    assert "GPS" in client.get("/").text
    assert client.get("/gps/latest").json() == {"status": "No GPS data"}
    assert client.post("/gps", json={"latitude": 1, "longitude": 2, "accuracy": 3}).status_code == 200
    assert client.get("/gps/latest").json()["latitude"] == 1
    assert client.post("/gps", json={"latitude": 999, "longitude": 2, "accuracy": 3}).status_code == 422
