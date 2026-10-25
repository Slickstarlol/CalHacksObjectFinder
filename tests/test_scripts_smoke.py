"""Run webcam.py and register.py's camera loops against a fake camera."""

import functools
import sys
from pathlib import Path

import cv2
import pytest

pytest.importorskip("ultralytics")
pytestmark = pytest.mark.slow

FIX = Path(__file__).parent / "fixtures"
FRAMES = [FIX / "1792851254577_laptop.jpg", FIX / "1792851263393_bottle.jpg"]


class FakeCamera:
    def __init__(self, paths, repeat=3):
        self.frames = [cv2.imread(str(p)) for p in paths for _ in range(repeat)]

    def isOpened(self):
        return True

    def set(self, *a):
        return True

    def read(self):
        return (True, self.frames.pop(0)) if self.frames else (False, None)

    def release(self):
        pass


def fake_keys(seq):
    it = iter(seq)
    return lambda *_: next(it, ord("q"))


def test_register_capture_loop_saves_photo(tmp_path, monkeypatch):
    import register as reg
    monkeypatch.setattr(reg, "BELONGINGS_DIR", tmp_path / "b")
    monkeypatch.setattr(reg.cv2, "VideoCapture", lambda *a: FakeCamera(FRAMES))
    monkeypatch.setattr(reg.cv2, "imshow", lambda *a: None)
    monkeypatch.setattr(reg.cv2, "destroyAllWindows", lambda *a: None)
    monkeypatch.setattr(reg.cv2, "waitKey", fake_keys([0, ord("s"), 0, ord("s")]))

    folder, info = reg.create_item("My Bottle", "bottle")
    n = reg.capture_from_webcam(reg.load_model(), folder, info, 0)
    assert n == 2
    assert len(list(folder.glob("reference_*.jpg"))) == 2


def test_webcam_main_records_my_bottle(tmp_path, monkeypatch):
    import db as dbmod
    import recognition
    import webcam
    import register as reg

    bdir = tmp_path / "b"
    monkeypatch.setattr(reg, "BELONGINGS_DIR", bdir)
    folder, _ = reg.create_item("My Bottle", "bottle")
    reg.import_images(reg.load_model(), folder, "bottle", [FRAMES[0]])

    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "m.db")
    monkeypatch.setattr(webcam, "BelongingMatcher",
                        functools.partial(recognition.BelongingMatcher, base_dir=bdir))
    monkeypatch.setattr(webcam, "SightingRecorder",
                        functools.partial(webcam.SightingRecorder,
                                          snapshot_dir=tmp_path / "s"))
    monkeypatch.setattr(webcam, "get_gps", lambda: None)
    monkeypatch.setattr(webcam.cv2, "VideoCapture", lambda *a: FakeCamera(FRAMES[1:]))
    monkeypatch.setattr(webcam.cv2, "imshow", lambda *a: None)
    monkeypatch.setattr(webcam.cv2, "destroyAllWindows", lambda *a: None)
    monkeypatch.setattr(webcam.cv2, "waitKey", lambda *a: 0)

    class _SilentPublisher:
        remote_enroll = None     # LivePublisher: enrollment state from the server

        def __init__(self, *a, **k):
            pass

        def submit(self, *a, **k):
            pass

        def close(self):
            pass

    monkeypatch.setattr(webcam, "LivePublisher", _SilentPublisher)
    monkeypatch.setattr(sys, "argv", ["webcam.py"])

    webcam.main()

    with dbmod.connect() as db:
        rows = db.execute("SELECT belonging_name, event FROM sightings").fetchall()
    assert [tuple(r) for r in rows] == [("My Bottle", "seen")]
