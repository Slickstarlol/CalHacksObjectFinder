"""Five still angles, same YOLO category, and the shots that already landed stay."""

import json
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from recallai.memory import db as dbmod
from recallai.vision.enroll import AngleCapture, save_angle
from recallai.vision.recognition import BelongingMatcher, HistogramEmbedder


def det(category, box):
    return SimpleNamespace(category=category, xyxy=box)


STILL = (10, 10, 120, 140)
CUP = (200, 20, 280, 160)


def hold_still(cap, category, box, t0=0.0):
    """Feed one steady box until the capture says to save it."""
    step = cap.step([det(category, box)], t0)
    assert step.save is False
    step = cap.step([det(category, box)], t0 + cap.still_for + 0.05)
    assert step.save is True
    assert step.category == category
    return step


def test_still_box_saves_then_waits_for_a_turn():
    cap = AngleCapture()
    step = hold_still(cap, "mouse", STILL, 0.0)
    cap.note_saved("mouse", 1, step.box, "mouse", "m1", 0.0 + cap.still_for)
    assert cap.saved == 1
    assert cap.phase == "turn"

    mid = cap.still_for + cap.turn_for / 2
    assert cap.step([det("mouse", STILL)], mid).save is False
    assert cap.phase == "turn"
    assert "Turn the mouse" in cap.message

    after = cap.still_for + cap.turn_for + 0.05
    assert cap.step([det("mouse", STILL)], after).save is False
    nxt = cap.step([det("mouse", STILL)], after + cap.still_for + 0.05)
    assert nxt.save is True
    assert nxt.category == "mouse"


def test_a_different_label_does_not_drop_saved_angles():
    cap = AngleCapture()
    step = hold_still(cap, "mouse", STILL)
    cap.note_saved("mouse", 1, step.box, "mouse", "m1", cap.still_for)
    # A single flipped frame is ignored.
    assert cap.step([det("cup", CUP)], cap.still_for + 0.1).save is False
    assert cap.saved == 1
    tripped = cap.step([det("cup", CUP)], cap.still_for + cap.miss_for + 0.2)
    assert tripped.save is False
    assert cap.phase == "retry"
    assert cap.saved == 1
    assert "cup" in cap.message and "mouse" in cap.message


def test_a_miss_asks_for_a_retry_and_keeps_the_count():
    cap = AngleCapture()
    cap.category = "mouse"
    cap.saved = 2
    cap.name = "mouse"
    assert cap.step([], 0.0).save is False
    assert cap.saved == 2
    missed = cap.step([], cap.miss_for + 0.05)
    assert missed.save is False
    assert cap.phase == "retry"
    assert cap.saved == 2
    assert "don't see" in cap.message


def test_a_person_in_frame_is_not_the_object():
    cap = AngleCapture()
    person = (0, 0, 500, 500)  # much bigger than the thing being held
    step = cap.step([det("person", person), det("mouse", STILL)], 0.0)
    assert step.save is False
    assert step.box == tuple(float(v) for v in STILL)
    saved = cap.step(
        [det("person", person), det("mouse", STILL)],
        cap.still_for + 0.05,
    )
    assert saved.save is True
    assert saved.category == "mouse"


def test_only_people_asks_for_an_object_and_saves_nothing():
    cap = AngleCapture()
    assert cap.step([det("person", (0, 0, 400, 400))], 0.0).save is False
    step = cap.step([det("person", (0, 0, 400, 400))], cap.miss_for + 0.05)
    assert step.save is False
    assert cap.phase == "retry"
    assert cap.saved == 0
    assert cap.category is None
    assert "People don't count" in cap.message


def test_label_change_before_the_first_save_does_not_lock_in():
    cap = AngleCapture()
    assert cap.step([det("mouse", STILL)], 0.0).save is False
    flipped = cap.miss_for + 0.05
    assert cap.step([det("cup", CUP)], flipped).save is False
    assert cap.step([det("cup", CUP)], flipped + cap.miss_for + 0.05).save is False
    assert cap.phase == "retry"
    assert cap.category is None
    assert cap.saved == 0
    step = hold_still(cap, "cup", CUP, t0=2.0)
    assert step.category == "cup"


def test_five_angles_keep_partial_photos_and_then_track(tmp_path):
    crop = np.zeros((32, 32, 3), np.uint8)
    info, count = save_angle(tmp_path, "mouse", crop)
    assert count == 1 and info["tracking"] is False
    assert info["name"] == "mouse"
    # A failed later angle never deletes the first file.
    first = list((tmp_path / info["id"]).glob("reference_*.jpg"))
    assert len(first) == 1

    for n in range(2, 5):
        info, count = save_angle(tmp_path, "mouse", crop, info["id"])
        assert count == n and info["tracking"] is False
    info, count = save_angle(tmp_path, "mouse", crop, info["id"])
    assert count == 5 and info["tracking"] is True
    assert len(list((tmp_path / info["id"]).glob("reference_*.jpg"))) == 5

    again, again_count = save_angle(tmp_path, "mouse", crop)
    assert again["id"] != info["id"]
    assert again["name"] == "mouse 2"
    assert again_count == 1 and again["tracking"] is False


def test_unfinished_item_is_continued_not_replaced(tmp_path):
    crop = np.zeros((32, 32, 3), np.uint8)
    info, _ = save_angle(tmp_path, "cup", crop)
    info, count = save_angle(tmp_path, "cup", crop)
    assert count == 2
    resumed, count = save_angle(tmp_path, "cup", crop)
    assert resumed["id"] == info["id"]
    assert count == 3
    assert len(list(tmp_path.iterdir())) == 1


def test_matcher_waits_until_tracking_is_on(tmp_path):
    from conftest import RED, make_object_image, register
    item_id = register(tmp_path, "mouse", "mouse",
                       [make_object_image(RED, seed=i) for i in range(5)])
    info_path = tmp_path / item_id / "info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["tracking"] = False
    info_path.write_text(json.dumps(info), encoding="utf-8")

    matcher = BelongingMatcher(HistogramEmbedder(), tmp_path)
    assert matcher.belongings == []
    assert [b.name for b in matcher.skipped] == ["mouse"]

    info["tracking"] = True
    info_path.write_text(json.dumps(info), encoding="utf-8")
    matcher.reload()
    assert [b.name for b in matcher.belongings] == ["mouse"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "m.db")
    dbmod.init_db()
    from recallai.api import server
    server.reset_enroll()
    return TestClient(server.app)


def test_enroll_api_keeps_progress_when_cancelled(client):
    assert client.get("/api/enroll").json()["active"] is False
    started = client.post("/api/enroll/start").json()
    assert started["active"] is True
    assert started["saved"] == 0
    assert "hold it still" in started["message"]

    reported = client.post("/api/live", json={"enroll": {
        "phase": "turn",
        "message": "Turn the mouse to a new angle. 2 of 5 saved.",
        "category": "mouse",
        "name": "mouse",
        "saved": 2,
        "belonging_id": "abc",
    }})
    assert reported.status_code == 200
    assert reported.json()["enroll"]["saved"] == 2

    cancelled = client.post("/api/enroll/cancel").json()
    assert cancelled["active"] is False
    # A frame still in flight cannot move the count or turn the session back on.
    client.post("/api/live", json={"enroll": {
        "phase": "hold", "message": "nope", "saved": 4, "category": "mouse",
    }})
    status = client.get("/api/enroll").json()
    assert status["active"] is False
    assert status["saved"] == 2

    done = client.post("/api/enroll/start").json()
    assert done["active"] is True and done["saved"] == 0
    client.post("/api/live", json={"enroll": {
        "phase": "done",
        "message": "mouse is in the tracker.",
        "category": "mouse",
        "name": "mouse",
        "saved": 5,
        "belonging_id": "abc",
    }})
    finished = client.get("/api/enroll").json()
    assert finished["active"] is False
    assert finished["phase"] == "done"
    assert finished["saved"] == 5
