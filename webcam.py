"""RecallAI live camera: watch for YOUR belongings and remember where they were.

Run server.py first if you want phone GPS attached to sightings (optional).
"""

import argparse
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import requests
from ultralytics import YOLO

from interpreter import Interpreter
from live_feed import LIVE_URL, PUBLISH_EVERY, LivePublisher
from recognition import BelongingMatcher, load_embedder
from recorder import Detection, SightingRecorder

BASE_DIR = Path(__file__).resolve().parent

# ---------------------------------
# Configuration
# ---------------------------------
CAMERA_INDEX = 0
GPS_URL = "http://127.0.0.1:8000/gps/latest"
SAVE_INTERVAL = 15      # seconds between 'seen' snapshots per belonging
GONE_AFTER = 5          # seconds out of view before 'left_view' is saved
RECOGNIZE_EVERY = 0.5   # seconds between recognition passes (CLIP is slower than YOLO)
MAX_GPS_AGE = 30
DETECT_CONF = 0.35


def get_gps():
    try:
        response = requests.get(GPS_URL, timeout=1)
        response.raise_for_status()
        data = response.json()
        if "latitude" not in data:
            return None
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(data["timestamp"])).total_seconds()
        if age < 0 or age > MAX_GPS_AGE:
            return None
        return data
    except (requests.RequestException, KeyError, ValueError, TypeError):
        return None


def to_detections(results, names):
    return [
        Detection(
            category=names[int(box.cls[0])],
            confidence=float(box.conf[0]),
            xyxy=tuple(float(v) for v in box.xyxy[0]),
        )
        for box in results.boxes
    ]


class BackgroundGPS:
    """Polls the phone-GPS server in its own thread so a slow or missing
    server never stalls the video."""

    def __init__(self, interval=2.0):
        self.latest = None
        self.interval = interval
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            self.latest = get_gps()
            time.sleep(self.interval)


class RecognitionWorker:
    """Runs "is it mine?" (DINOv2) + saving in a background thread.

    The video loop hands over its latest frame; if the worker is still busy
    with the previous one, the older pending frame is simply replaced, so the
    video never waits and the worker always works on the freshest frame.
    """

    def __init__(self, make_recorder):
        self._make_recorder = make_recorder
        self._cond = threading.Condition()
        self._pending = None
        self._busy = False
        self._stop = False
        self.found = {}          # belonging_id -> (Match, Detection), last pass
        self.error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, frame, detections, gps, now):
        with self._cond:
            self._pending = (frame.copy(), detections, gps, now)
            self._cond.notify()

    @property
    def idle(self):
        with self._cond:
            return not self._busy and self._pending is None

    def _run(self):
        # sqlite connections must be used in the thread that created them
        recorder = self._make_recorder()
        try:
            while True:
                with self._cond:
                    while self._pending is None and not self._stop:
                        self._cond.wait()
                    if self._pending is None:  # stopped, nothing left to do
                        return
                    job, self._pending, self._busy = self._pending, None, True
                try:
                    self.found = recorder.process(*job)
                except Exception as e:  # keep the camera running
                    self.error = e
                    print(f"[recognition] error: {e}")
                finally:
                    with self._cond:
                        self._busy = False
        finally:
            recorder.close()

    def stop(self, timeout=10):
        """Finish the frame in progress (and one pending), then exit."""
        with self._cond:
            self._stop = True
            self._cond.notify()
        self._thread.join(timeout)


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def live_box(old_xyxy, detections):
    """Move a label onto the current frame's detection that overlaps it most,
    so the green box follows the object between recognition passes."""
    best = max(detections, key=lambda d: _iou(old_xyxy, d.xyxy), default=None)
    if best is not None and _iou(old_xyxy, best.xyxy) > 0.3:
        return best.xyxy
    return old_xyxy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=CAMERA_INDEX)
    parser.add_argument("--threshold", type=float, default=None,
                        help="match threshold (DINOv2 default 0.70). Raise it "
                             "if strangers' items get labelled as yours.")
    parser.add_argument("--embedder", default="dinov2",
                        choices=["dinov2", "clip", "histogram"])
    args = parser.parse_args()

    matcher = BelongingMatcher(load_embedder(args.embedder),
                               threshold=args.threshold)
    print(f"Recognition: {matcher.embedder.name}, threshold {matcher.threshold:.2f}")
    if not matcher.belongings:
        raise SystemExit(
            "No belongings with reference photos yet. Run: python register.py"
        )
    print("Watching for:",
          ", ".join(f"{b.name} ({b.category})" for b in matcher.belongings))

    model = YOLO(str(BASE_DIR / "yolo11n.pt"))
    interpreter = Interpreter()
    print("Grok interpretation:",
          "ON" if interpreter.enabled else "off (set XAI_API_KEY to enable)")
    worker = RecognitionWorker(lambda: SightingRecorder(
        matcher, save_interval=SAVE_INTERVAL, gone_after=GONE_AFTER,
        on_saved=interpreter.submit))
    gps = BackgroundGPS()

    camera = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    if not camera.isOpened():
        raise RuntimeError(f"Cannot open camera {args.camera}")
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # always show the newest frame

    last_publish = 0.0
    publisher = LivePublisher(LIVE_URL)
    last_recognize = 0.0
    fps, last_frame = 0.0, time.monotonic()

    print("RecallAI started - press Q to quit")
    print(f"Dashboard mirror: {LIVE_URL}")
    try:
        while True:
            ok, frame = camera.read()
            if not ok:
                break

            results = model(frame, conf=DETECT_CONF, verbose=False)[0]
            detections = to_detections(results, model.names)
            display = results.plot()
            now = time.monotonic()

            # Hand the frame to the background worker; never wait for it.
            if now - last_recognize >= RECOGNIZE_EVERY and worker.idle:
                worker.submit(frame, detections, gps.latest, now)
                last_recognize = now

            # Highlight your belongings in green with their name
            for m, det in list(worker.found.values()):
                x1, y1, x2, y2 = live_box(det.xyxy, detections)
                p1, p2 = (int(x1), int(y1)), (int(x2), int(y2))
                cv2.rectangle(display, p1, p2, (0, 255, 0), 3)
                cv2.putText(display, f"{m.name} {m.score:.2f}",
                            (p1[0], max(20, p1[1] - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            dt = now - last_frame
            last_frame = now
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1 / dt) if fps else 1 / dt
            gps_ok = gps.latest is not None
            cv2.putText(display, "GPS: ON" if gps_ok else "GPS: off (optional)",
                        (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (0, 255, 0) if gps_ok else (0, 165, 255), 2)
            cv2.putText(display, f"Mine in view: {len(worker.found)}   {fps:.0f} FPS",
                        (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.imshow("RecallAI - Visual Memory", display)

            if now - last_publish >= PUBLISH_EVERY:
                in_view = []
                for m, det in list(worker.found.values()):
                    x1, y1, x2, y2 = live_box(det.xyxy, detections)
                    in_view.append({
                        "belonging_id": m.belonging_id,
                        "name": m.name,
                        "category": m.category,
                        "score": round(m.score, 3),
                        "bbox": [int(x1), int(y1), int(x2), int(y2)],
                    })
                publisher.submit(display, {
                    "gps": gps_ok,
                    "in_view": in_view,
                    "detections": [
                        {
                            "category": d.category,
                            "confidence": round(d.confidence, 3),
                            "bbox": [int(v) for v in d.xyxy],
                        }
                        for d in detections[:30]
                    ],
                })
                last_publish = now

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        publisher.close()
        camera.release()
        worker.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
