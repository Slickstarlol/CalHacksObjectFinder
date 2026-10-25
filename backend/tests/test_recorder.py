"""Recorder logic: what gets saved, when, and with what context."""

from pathlib import Path

import numpy as np
import pytest

from recallai.memory import db as dbmod
from conftest import BLUE, RED, make_object_image, register
from recallai.vision.recognition import BelongingMatcher, HistogramEmbedder
from recallai.vision.recorder import Detection, SightingRecorder

GPS = {"latitude": 37.8, "longitude": -122.4, "accuracy": 12.0}


def scene(obj_colors):
    """640x480 frame with objects pasted at fixed spots; returns frame + boxes."""
    frame = np.full((480, 640, 3), 90, np.uint8)
    boxes = []
    for i, color in enumerate(obj_colors):
        x, y = 40 + i * 200, 150
        frame[y:y + 120, x:x + 160] = make_object_image(color, seed=100 + i)
        boxes.append((x, y, x + 160, y + 120))
    return frame, boxes


@pytest.fixture
def rec(tmp_path, belongings_dir):
    register(belongings_dir, "My Bag", "backpack",
             [make_object_image(RED, seed=i) for i in range(5)], item_id="bag1")
    matcher = BelongingMatcher(HistogramEmbedder(), belongings_dir)
    r = SightingRecorder(matcher, db_path=tmp_path / "m.db",
                         snapshot_dir=tmp_path / "snaps",
                         save_interval=15, gone_after=5)
    r.tmp = tmp_path
    yield r
    r.close()


def rows(rec):
    return rec.db.execute("SELECT * FROM sightings ORDER BY id").fetchall()


def test_saves_my_bag_with_context(rec):
    frame, (red_box, _) = scene([RED, BLUE])
    dets = [Detection("backpack", 0.9, red_box),
            Detection("laptop", 0.8, (300, 150, 460, 270)),
            Detection("bottle", 0.7, (500, 100, 540, 200))]
    found = rec.process(frame, dets, gps=GPS, now=0)
    assert set(found) == {"bag1"}

    (r,) = rows(rec)
    assert r["belonging_name"] == "My Bag" and r["event"] == "seen"
    assert r["nearby"] == "bottle, laptop"
    assert r["latitude"] == 37.8 and r["match_score"] >= 0.8
    assert r["bbox"] == "40,150,200,270"
    snaps = rec.tmp / "snaps"
    assert (snaps / Path(r["image_path"]).name).exists()
    assert (snaps / "crops" / Path(r["crop_path"]).name).exists()


def test_strangers_backpack_is_not_saved(rec):
    frame, (_, blue_box) = scene([RED, BLUE])
    found = rec.process(frame, [Detection("backpack", 0.9, blue_box)], now=0)
    assert found == {} and rows(rec) == []


def test_unregistered_category_is_ignored(rec):
    frame, (red_box, _) = scene([RED, BLUE])
    rec.process(frame, [Detection("laptop", 0.9, red_box)], now=0)
    assert rows(rec) == []


def test_works_without_gps(rec):
    frame, (red_box,) = scene([RED])
    rec.process(frame, [Detection("backpack", 0.9, red_box)], gps=None, now=0)
    (r,) = rows(rec)
    assert r["latitude"] is None


def test_seen_is_throttled(rec):
    frame, (box,) = scene([RED])
    det = [Detection("backpack", 0.9, box)]
    for t in [0, 1, 5, 14.9]:
        rec.process(frame, det, now=t)
    assert len(rows(rec)) == 1
    rec.process(frame, det, now=15)
    assert len(rows(rec)) == 2


def test_left_view_saved_once_with_last_frame(rec):
    frame, (box,) = scene([RED])
    empty = np.full_like(frame, 90)
    rec.process(frame, [Detection("backpack", 0.9, box)], now=0)
    rec.process(frame, [Detection("backpack", 0.9, box)], now=2)  # last time seen
    rec.process(empty, [], now=4)       # gone 2s: not yet
    assert [r["event"] for r in rows(rec)] == ["seen"]
    rec.process(empty, [], now=7.5)     # gone 5.5s
    rec.process(empty, [], now=20)      # no duplicate
    events = [r["event"] for r in rows(rec)]
    assert events == ["seen", "left_view"]
    # left_view snapshot is the frame where the bag WAS visible
    left = rows(rec)[-1]
    assert left["bbox"] == "40,150,200,270"


def test_reappearing_resets_left_view(rec):
    frame, (box,) = scene([RED])
    det = [Detection("backpack", 0.9, box)]
    rec.process(frame, det, now=0)
    rec.process(frame, [], now=10)    # left_view #1
    rec.process(frame, det, now=30)   # back (also a new 'seen')
    rec.process(frame, [], now=40)    # left_view #2
    # Each visit was a single frame, so its 'seen' row is relabelled
    # rather than storing the same snapshot twice.
    assert [r["event"] for r in rows(rec)] == ["left_view", "left_view"]


def test_seen_once_then_left_is_one_row_not_two(rec):
    frame, (box,) = scene([RED])
    rec.process(frame, [Detection("backpack", 0.9, box)], now=0)
    rec.process(frame, [], now=6)
    assert [r["event"] for r in rows(rec)] == ["left_view"]


def test_second_label_on_same_object_is_not_nearby(rec):
    frame, (box,) = scene([RED])
    x1, y1, x2, y2 = box
    dets = [Detection("backpack", 0.9, box),
            Detection("handbag", 0.5, (x1 + 4, y1 + 2, x2 - 3, y2)),  # same object
            Detection("cup", 0.6, (500, 50, 560, 120))]                # real neighbour
    rec.process(frame, dets, now=0)
    assert rows(rec)[0]["nearby"] == "cup"


def test_two_matching_detections_save_one_row(rec):
    frame = np.full((480, 640, 3), 90, np.uint8)
    frame[150:270, 40:200] = make_object_image(RED, seed=1)
    frame[150:270, 300:460] = make_object_image(RED, seed=2)
    dets = [Detection("backpack", 0.6, (40, 150, 200, 270)),
            Detection("backpack", 0.9, (300, 150, 460, 270))]
    found = rec.process(frame, dets, now=0)
    assert list(found) == ["bag1"] and len(rows(rec)) == 1


def test_deleted_belonging_is_forgotten_not_logged_as_left(rec):
    import shutil
    frame, (red_box,) = scene([RED])
    rec.process(frame, [Detection("backpack", 0.9, red_box)], now=0)
    assert len(rows(rec)) == 1
    shutil.rmtree(rec.matcher.base_dir / "bag1")    # deleted on the dashboard
    rec.matcher.reload()
    rec.process(frame, [], now=100)                 # long gone: would log left_view
    assert [r["event"] for r in rows(rec)] == ["seen"]
    assert "bag1" not in rec.last_seen
