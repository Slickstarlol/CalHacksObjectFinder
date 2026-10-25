"""The video loop must never wait for recognition (DINOv2) or GPS."""

import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("ultralytics")
from recallai.vision import webcam


class FakeCamera:
    def __init__(self, n):
        self.n = n

    def isOpened(self):
        return True

    def set(self, *a):
        return True

    def read(self):
        if self.n == 0:
            return False, None
        self.n -= 1
        time.sleep(0.02)                     # ~50 FPS camera
        return True, np.zeros((480, 640, 3), np.uint8)

    def release(self):
        pass


class FakeYOLO:
    names = {0: "mouse"}

    def __init__(self, *a):
        pass

    def __call__(self, frame, **kw):
        box = SimpleNamespace(cls=[0], conf=[0.9], xyxy=[[10.0, 10.0, 100.0, 100.0]])
        return [SimpleNamespace(boxes=[box], plot=lambda: frame.copy())]


class SlowRecorder:
    """Stands in for DINOv2 on a laptop CPU: 0.4 s per pass."""
    calls = 0

    def __init__(self, *a, **kw):
        pass

    def process(self, frame, detections, gps, now):
        SlowRecorder.calls += 1
        time.sleep(0.4)
        m = SimpleNamespace(belonging_id="fake-id", name="My Mouse", score=0.9, category="mouse")
        return {"mouse1": (m, detections[0])}

    def close(self):
        pass


def test_video_never_freezes_while_recognition_runs(monkeypatch):
    shown = []
    matcher = SimpleNamespace(belongings=[SimpleNamespace(name="My Mouse", category="mouse")],
                              embedder=SimpleNamespace(name="fake"), threshold=0.7)
    monkeypatch.setattr(webcam, "BelongingMatcher", lambda *a, **k: matcher)
    monkeypatch.setattr(webcam, "PeopleMatcher", lambda *a, **k: None)
    monkeypatch.setattr(webcam, "load_embedder", lambda *a: None)
    monkeypatch.setattr(webcam, "YOLO", FakeYOLO)
    monkeypatch.setattr(webcam, "SightingRecorder", SlowRecorder)
    monkeypatch.setattr(webcam, "Interpreter", lambda: SimpleNamespace(enabled=False, submit=None))
    monkeypatch.setattr(webcam, "get_gps", lambda: time.sleep(1.0))  # dead GPS server
    monkeypatch.setattr(webcam.cv2, "VideoCapture", lambda *a: FakeCamera(100))
    monkeypatch.setattr(webcam.cv2, "imshow", lambda *a: shown.append(time.monotonic()))
    monkeypatch.setattr(webcam.cv2, "destroyAllWindows", lambda *a: None)
    monkeypatch.setattr(webcam.cv2, "waitKey", lambda *a: 0)
    monkeypatch.setattr(sys, "argv", ["webcam.py"])
    SlowRecorder.calls = 0

    webcam.main()

    gaps = np.diff(shown)
    assert len(shown) == 100
    assert SlowRecorder.calls >= 3                 # recognition still happened
    assert gaps.max() < 0.15, f"video froze for {gaps.max():.2f}s"


def test_live_box_follows_object():
    dets = [SimpleNamespace(xyxy=(20, 10, 110, 100)), SimpleNamespace(xyxy=(400, 400, 450, 450))]
    assert webcam.live_box((10, 10, 100, 100), dets) == (20, 10, 110, 100)
    assert webcam.live_box((200, 200, 250, 250), dets) == (200, 200, 250, 250)  # nothing near


def test_worker_survives_errors_and_flushes_on_stop():
    class Boom:
        n = 0
        def process(self, *a):
            Boom.n += 1
            if Boom.n == 1:
                raise RuntimeError("bad frame")
            return {"x": 1}
        def close(self):
            pass
    w = webcam.RecognitionWorker(Boom)
    f = np.zeros((4, 4, 3), np.uint8)
    w.submit(f, [], None, 0)
    while not w.idle:
        time.sleep(0.01)
    w.submit(f, [], None, 1)   # pending when stop() is called
    w.stop()
    assert w.found == {"x": 1} and isinstance(w.error, RuntimeError)
