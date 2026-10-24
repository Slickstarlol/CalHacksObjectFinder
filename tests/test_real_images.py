"""End-to-end tests on real frames from this project's webcam (CalHacks hall).

Uses the real YOLO model and DINOv2 recognizer, so they are marked slow:
    pytest -m slow
First run downloads DINOv2 (~90 MB).
"""

from pathlib import Path

import cv2
import pytest

pytest.importorskip("transformers")
pytest.importorskip("ultralytics")

import register as reg
from recognition import BelongingMatcher, DinoEmbedder, crop_box
from recorder import SightingRecorder
from webcam import DETECT_CONF, to_detections

pytestmark = pytest.mark.slow

FIX = Path(__file__).parent / "fixtures"
T0 = FIX / "1792851254577_laptop.jpg"       # 07:14:14
T9 = FIX / "1792851263393_bottle.jpg"       # 07:14:23, camera moved
MOUSE_A = FIX / "1792851268568_cell_phone.jpg"  # gaming mouse, YOLO: "cell phone"
MOUSE_B = FIX / "1792851268759_remote.jpg"      # same mouse, YOLO: "cell phone"+"remote"


@pytest.fixture(scope="module")
def yolo():
    return reg.load_model()


@pytest.fixture(scope="module")
def dino():
    return DinoEmbedder()


def detect(yolo, path):
    frame = cv2.imread(str(path))
    return frame, to_detections(yolo(frame, conf=DETECT_CONF, verbose=False)[0],
                                yolo.names)


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    return inter / (area(a) + area(b) - inter)


@pytest.fixture
def env(tmp_path, monkeypatch, dino):
    bdir = tmp_path / "belongings"
    monkeypatch.setattr(reg, "BELONGINGS_DIR", bdir)

    def recorder():
        m = BelongingMatcher(dino, bdir)
        return SightingRecorder(m, db_path=tmp_path / "m.db",
                                snapshot_dir=tmp_path / "snaps")
    return bdir, recorder


def test_register_bottle_then_recognise_it_9s_later(env, yolo):
    bdir, make_recorder = env
    folder, info = reg.create_item("My Bottle", "bottle")
    assert reg.import_images(yolo, folder, "bottle", [T0]) == 1
    assert (folder / "reference_1.jpg").exists()

    rec = make_recorder()
    frame, dets = detect(yolo, T9)
    found = rec.process(frame, dets, now=0)
    rows = rec.db.execute("SELECT * FROM sightings").fetchall()
    rec.close()

    assert [m.name for m, _ in found.values()] == ["My Bottle"]
    assert len(rows) == 1 and rows[0]["belonging_name"] == "My Bottle"
    assert "chair" in rows[0]["nearby"] and "laptop" in rows[0]["nearby"]


def test_my_chair_is_not_confused_with_other_chairs_in_room(env, yolo):
    bdir, make_recorder = env
    folder, _ = reg.create_item("My Chair", "chair")
    my_chair_t9 = (435, 315, 581, 479)
    reg.save_reference(folder, crop_box(cv2.imread(str(T9)), my_chair_t9))

    rec = make_recorder()
    frame, dets = detect(yolo, T0)  # several white folding chairs in view
    chairs = [d for d in dets if d.category == "chair"]
    assert len(chairs) >= 4
    matches = rec.matcher.match_many([crop_box(frame, d.xyxy) for d in chairs],
                                     ["chair"] * len(chairs))
    hits = [d.xyxy for d, m in zip(chairs, matches) if m]
    rec.close()

    assert len(hits) == 1, "only my chair should match"
    assert iou(hits[0], (433, 327, 574, 479)) > 0.8


def test_gaming_mouse_registered_as_mouse_found_when_yolo_says_phone(env, yolo):
    bdir, make_recorder = env
    folder, _ = reg.create_item("My Gaming Mouse", "mouse")
    # register.py accepts the "cell phone" detection for a "mouse" item
    assert reg.import_images(yolo, folder, "mouse", [MOUSE_A]) == 1

    rec = make_recorder()
    frame, dets = detect(yolo, MOUSE_B)
    assert "mouse" not in {d.category for d in dets}  # YOLO never says mouse
    found = rec.process(frame, dets, now=0)
    rec.close()
    assert [m.name for m, _ in found.values()] == ["My Gaming Mouse"]


def test_registered_items_not_in_frame_produce_nothing(env, yolo):
    bdir, make_recorder = env
    folder, _ = reg.create_item("My Bottle", "bottle")
    reg.import_images(yolo, folder, "bottle", [T0])

    rec = make_recorder()
    frame, dets = detect(yolo, MOUSE_B)  # close-up of the mouse, no bottle
    assert rec.process(frame, dets, now=0) == {}
    assert rec.db.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] == 0
    rec.close()
