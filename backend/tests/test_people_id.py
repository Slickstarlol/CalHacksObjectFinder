"""Naming people on the live feed: reference photos, teaching, matching."""

import time
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from recallai import paths
from recallai.memory import db as dbmod
from recallai.vision.people_id import (PeopleMatcher, person_folder, save_reference,
                                       teach)
from recallai.vision.recognition import HistogramEmbedder
from recallai.vision.recorder import Detection
from conftest import BLUE, GREEN, RED, make_object_image


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    monkeypatch.setattr(dbmod, "DB_PATH", path)
    dbmod.init_db(path)
    return path


def add_person(db_path, name, owner_id=1):
    now = datetime.now(timezone.utc).isoformat()
    with dbmod.connect(db_path) as db:
        cur = db.execute(
            "INSERT INTO people (name, description, relationship, known_since, "
            "created_at, updated_at, owner_id) VALUES (?, '', '', NULL, ?, ?, ?)",
            (name, now, now, owner_id))
        db.commit()
    return cur.lastrowid


class CountingEmbedder(HistogramEmbedder):
    def __init__(self):
        self.calls = 0

    def embed(self, images_bgr):
        self.calls += len(images_bgr)
        return super().embed(images_bgr)


def scene(*colors, w=480, h=240):
    """A grey frame with one coloured 'person' per colour, side by side.
    Returns (frame, [Detection])."""
    frame = np.full((h, w, 3), 128, np.uint8)
    dets = []
    step = w // len(colors)
    for i, color in enumerate(colors):
        x1 = i * step + 20
        frame[30:220, x1:x1 + 100] = make_object_image(color, size=(190, 100), seed=i + 7)
        dets.append(Detection("person", 0.9, (x1, 30, x1 + 100, 220)))
    return frame, dets


def test_save_reference_keeps_the_newest(tmp_path):
    crop = make_object_image(RED)
    for _ in range(5):
        save_reference(3, crop, base_dir=tmp_path, keep=3)
        time.sleep(0.002)
    files = sorted(p.name for p in (tmp_path / "3").iterdir())
    assert len(files) == 3 and all(f.startswith("reference_") for f in files)


def test_teach_snaps_to_the_person_and_scales_from_the_preview():
    frame, dets = scene(RED, BLUE, w=960)
    dets.append(Detection("chair", 0.8, (0, 0, 960, 240)))
    # The dashboard saw a 480-wide preview; the click box is a little off.
    job = {"person_id": 5, "bbox": [262, 18, 318, 108], "width": 480}
    saved = teach(frame, dets, job)
    assert saved is not None and saved.parent == person_folder(5)
    assert saved.parent.parent == paths.PEOPLE_DIR

    nobody = {"person_id": 5, "bbox": [0, 200, 20, 239], "width": 480}
    assert teach(frame, dets, nobody) is None


def test_matcher_names_each_person(db_path):
    maya, sam = add_person(db_path, "Maya"), add_person(db_path, "Sam")
    save_reference(maya, make_object_image(RED, size=(190, 100), seed=1))
    save_reference(sam, make_object_image(BLUE, size=(190, 100), seed=2))
    embedder = CountingEmbedder()
    matcher = PeopleMatcher(embedder, db_path=db_path).reload()
    assert embedder.calls == 2

    frame, dets = scene(BLUE, GREEN, RED)
    found = {pm.name: det for pm, det in matcher.match(frame, dets)}
    assert set(found) == {"Maya", "Sam"}             # the green stranger stays unnamed
    assert found["Sam"] is dets[0] and found["Maya"] is dets[2]

    # A rename is just a new name: no photo is embedded again.
    with dbmod.connect(db_path) as db:
        db.execute("UPDATE people SET name='Maya C.' WHERE id=?", (maya,))
        db.execute("DELETE FROM people WHERE id=?", (sam,))
        db.commit()
    before = embedder.calls
    matcher.reload()
    assert embedder.calls == before
    assert [pm.name for pm, _ in matcher.match(frame, dets)] == ["Maya C."]


def test_one_name_per_box_and_one_box_per_name_per_account(db_path):
    maya = add_person(db_path, "Maya", owner_id=1)
    other = add_person(db_path, "M", owner_id=2)     # another account, same look
    for pid in (maya, other):
        save_reference(pid, make_object_image(RED, size=(190, 100), seed=pid))
    matcher = PeopleMatcher(HistogramEmbedder(), db_path=db_path).reload()

    frame, dets = scene(RED, RED)
    found = matcher.match(frame, dets)
    by_owner = {}
    for pm, det in found:
        by_owner.setdefault(pm.owner_id, []).append(det)
    assert sorted(by_owner) == [1, 2]
    assert all(len(boxes) == 1 for boxes in by_owner.values())


def test_matcher_without_photos_does_nothing(db_path):
    add_person(db_path, "Maya")
    embedder = CountingEmbedder()
    matcher = PeopleMatcher(embedder, db_path=db_path).reload()
    frame, dets = scene(RED)
    assert matcher.match(frame, dets) == [] and embedder.calls == 0


def test_worker_matches_people_and_reloads_on_request():
    pytest.importorskip("ultralytics")
    from recallai.vision import webcam

    class Recorder:
        def process(self, *a):
            return {}

        def close(self):
            pass

    class People:
        reloads = 0

        def reload(self):
            People.reloads += 1

        def match(self, frame, detections):
            return [(SimpleNamespace(person_id=1, name="Maya", score=0.9), detections[0])]

    det = Detection("person", 0.9, (1, 1, 3, 3))
    w = webcam.RecognitionWorker(Recorder, people=People())
    f = np.zeros((4, 4, 3), np.uint8)
    try:
        for _ in range(2):
            w.submit(f, [det], None, 0)
            while not w.idle:
                time.sleep(0.01)
        assert People.reloads == 1                       # names cached between frames
        w.request_people_reload()
        w.submit(f, [det], None, 1)
        while not w.idle:
            time.sleep(0.01)
    finally:
        w.stop()
    assert People.reloads == 2
    assert w.people_found[0][0].name == "Maya"


def test_detection_labels_skip_your_belongings():
    pytest.importorskip("ultralytics")
    from recallai.vision import webcam

    blank = np.zeros((120, 160, 3), np.uint8)
    det = Detection("laptop", 0.913, (20, 40, 120, 110))
    drawn = blank.copy()
    webcam.draw_detections(drawn, [det])
    assert drawn.any()
    skipped = blank.copy()
    webcam.draw_detections(skipped, [det], skip=[(22, 40, 120, 112)])
    assert not skipped.any()


def test_boxes_are_drawn_as_corners_only():
    from recallai.vision.recognition import draw_corners
    img = np.zeros((120, 120, 3), np.uint8)
    draw_corners(img, (10, 10, 110, 110), (0, 255, 0), 2)
    assert img[10, 10].any() and img[110, 110].any() and img[10, 25].any()   # the corners
    assert not img[10, 60].any() and not img[60, 10].any()                   # edges stay open
    assert not img[60, 60].any()
