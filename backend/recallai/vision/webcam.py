"""RecallAI live camera: watch for YOUR belongings and remember where they were.

Run it with:  python -m recallai.vision.webcam  [--camera 0]
Start the server first (python -m uvicorn recallai.api.server:app --port 8000)
so the dashboard shows the live frame and phone GPS is attached (optional).

The camera is a trusted device, not a user: it recognizes every account's
belongings and each sighting is tagged with the matched belonging's owner
(and that owner's phone GPS). See docs/AUTH.md.
"""

import argparse
import threading
import time
from datetime import datetime, timezone

import cv2
import requests
from ultralytics import YOLO

from recallai import paths
from recallai.vision.enroll import AngleCapture, save_angle_default
from recallai.ai.interpreter import Interpreter
from recallai.vision.live_feed import (LIVE_URL, PUBLISH_EVERY, SERVER_URL, LivePublisher,
                                      device_headers)
from recallai.vision.people_id import PERSON, PeopleMatcher, teach as teach_person
from recallai.vision.recognition import (BelongingMatcher, box_iou as _iou, crop_box,
                                         draw_corners, load_embedder)
from recallai.vision.recorder import Detection, SightingRecorder

# ---------------------------------
# Configuration
# ---------------------------------
CAMERA_INDEX = 0
GPS_URL = SERVER_URL + "/api/device/gps"   # every account's fresh phone GPS
SAVE_INTERVAL = 15      # seconds between 'seen' snapshots per belonging
GONE_AFTER = 5          # seconds out of view before 'left_view' is saved
RECOGNIZE_EVERY = 0.5   # seconds between recognition passes (CLIP is slower than YOLO)
MAX_GPS_AGE = 30
DETECT_CONF = 0.35
PEOPLE_REFRESH = 3.0    # seconds between re-reading people's names and photos


def _fresh(fix):
    try:
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(fix["timestamp"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return False
    return 0 <= age <= MAX_GPS_AGE


def get_gps():
    """{"by_owner": {"<user id>": fix}} with only fresh fixes, or None.
    The recorder attaches each belonging's owner's fix to its sightings."""
    try:
        response = requests.get(GPS_URL, timeout=1, headers=device_headers())
        response.raise_for_status()
        data = response.json()
        by_owner = {k: v for k, v in (data.get("by_owner") or {}).items()
                    if isinstance(v, dict) and "latitude" in v and _fresh(v)}
        return {"by_owner": by_owner} if by_owner else None
    except (requests.RequestException, AttributeError, ValueError, TypeError):
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

    With a PeopleMatcher it also names the person boxes it recognizes.
    """

    def __init__(self, make_recorder, people=None):
        self._make_recorder = make_recorder
        self.people = people
        self.people_found = []   # [(PersonMatch, Detection)], last pass
        self._people_loaded = None
        self._cond = threading.Condition()
        self._pending = None
        self._busy = False
        self._stop = False
        self.found = {}          # belonging_id -> (Match, Detection), last pass
        self.error = None
        self._reload = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, frame, detections, gps, now):
        with self._cond:
            self._pending = (frame.copy(), detections, gps, now)
            self._cond.notify()

    def request_reload(self):
        """The enrollment just finished; pick up its reference photos."""
        with self._cond:
            self._reload = True
            self._cond.notify()

    def request_people_reload(self):
        """A person was just named; use their new photo from the next frame."""
        self._people_loaded = None

    def _match_people(self, frame, detections):
        now = time.monotonic()
        if self._people_loaded is None or now - self._people_loaded >= PEOPLE_REFRESH:
            self._people_loaded = now
            self.people.reload()
        return self.people.match(frame, detections)

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
                    while self._pending is None and not self._reload and not self._stop:
                        self._cond.wait()
                    if self._pending is None and not self._reload:  # stopped
                        return
                    job, self._pending = self._pending, None
                    reload, self._reload = self._reload, False
                    self._busy = job is not None
                try:
                    if reload:
                        recorder.matcher.reload()
                        print("Now tracking:", ", ".join(
                            b.name for b in recorder.matcher.belongings) or "(nothing)")
                    if job is not None:
                        self.found = recorder.process(*job)
                        if self.people is not None:
                            self.people_found = self._match_people(job[0], job[1])
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


def live_box(old_xyxy, detections):
    """Move a label onto the current frame's detection that overlaps it most,
    so the green box follows the object between recognition passes."""
    best = max(detections, key=lambda d: _iou(old_xyxy, d.xyxy), default=None)
    if best is not None and _iou(old_xyxy, best.xyxy) > 0.3:
        return best.xyxy
    return old_xyxy


# Box colours (BGR) for YOLO categories; a category keeps its colour.
PALETTE = [(56, 56, 255), (151, 157, 255), (31, 112, 255), (29, 178, 255),
           (49, 210, 207), (10, 249, 72), (23, 204, 146), (134, 219, 61),
           (211, 188, 0), (209, 117, 0), (255, 149, 0), (255, 56, 132)]
PERSON_COLOR = (255, 170, 60)


def _color(category):
    if category == PERSON:
        return PERSON_COLOR
    return PALETTE[sum(category.encode()) % len(PALETTE)]


def draw_detections(display, detections, skip=()):
    """YOLO boxes labelled "laptop 91%" (results.plot() printed 0.91).
    Boxes overlapping one in `skip` (your recognized belongings, drawn
    separately in green) are left out so labels don't pile up."""
    font, scale = cv2.FONT_HERSHEY_SIMPLEX, 0.5
    for d in detections:
        if any(_iou(d.xyxy, s) > 0.5 for s in skip):
            continue
        color = _color(d.category)
        x1, y1, x2, y2 = (int(v) for v in d.xyxy)
        draw_corners(display, (x1, y1, x2, y2), color, 2)
        label = f"{d.category} {d.confidence:.0%}"
        (tw, th), _ = cv2.getTextSize(label, font, scale, 1)
        top = y1 - th - 8 if y1 >= th + 8 else y1      # above the box, or just inside
        cv2.rectangle(display, (x1, top), (x1 + tw + 8, top + th + 8), color, -1)
        cv2.putText(display, label, (x1 + 4, top + th + 3), font, scale,
                    (255, 255, 255), 1, cv2.LINE_AA)


def _paint_enroll(display, message):
    """Prompt along the bottom of the frame the dashboard is mirroring.
    Returns the height of the strip it drew (0 when there is no prompt)."""
    if not message:
        return 0
    h, w = display.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale, thick = 0.55, 1
    (tw, th), _ = cv2.getTextSize(message, font, scale, thick)
    while tw > w - 24 and len(message) > 12:
        message = message[:-4].rstrip() + "..."
        (tw, th), _ = cv2.getTextSize(message, font, scale, thick)
    cv2.rectangle(display, (0, h - th - 22), (w, h), (20, 16, 12), -1)
    cv2.putText(display, message, (12, h - 10), font, scale,
                (240, 230, 210), thick, cv2.LINE_AA)
    return th + 22


def _paint_fps(display, fps, bottom=0):
    """Small FPS readout in the bottom-right corner, above `bottom` pixels."""
    h, w = display.shape[:2]
    text = f"{fps:.0f} FPS"
    font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
    (tw, th), _ = cv2.getTextSize(text, font, scale, thick)
    x, y = w - tw - 12, h - bottom - 10
    cv2.rectangle(display, (x - 6, y - th - 6), (w - 6, y + 6), (20, 16, 12), -1)
    cv2.putText(display, text, (x, y), font, scale, (240, 230, 210), thick, cv2.LINE_AA)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=CAMERA_INDEX)
    parser.add_argument("--threshold", type=float, default=None,
                        help="match threshold (DINOv2 default 0.70). Raise it "
                             "if strangers' items get labelled as yours.")
    parser.add_argument("--embedder", default="dinov2",
                        choices=["dinov2", "clip", "histogram"])
    parser.add_argument("--person-threshold", type=float, default=None,
                        help="how sure it must be to name a person (DINOv2 "
                             "default 0.75). Raise it if strangers get names.")
    args = parser.parse_args()

    matcher = BelongingMatcher(load_embedder(args.embedder),
                               threshold=args.threshold)
    print(f"Recognition: {matcher.embedder.name}, threshold {matcher.threshold:.2f}")
    if not matcher.belongings:
        raise SystemExit(
            "No belongings with reference photos yet. Run: python -m recallai.vision.register"
        )
    print("Watching for:",
          ", ".join(f"{b.name} ({b.category})" for b in matcher.belongings))

    model = YOLO(str(paths.YOLO_PATH))
    interpreter = Interpreter()
    print("Grok interpretation:",
          "ON" if interpreter.enabled else "off (set XAI_API_KEY to enable)")
    people = PeopleMatcher(matcher.embedder, threshold=args.person_threshold)
    worker = RecognitionWorker(lambda: SightingRecorder(
        matcher, save_interval=SAVE_INTERVAL, gone_after=GONE_AFTER,
        on_saved=interpreter.submit), people=people)
    gps = BackgroundGPS()

    camera = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    if not camera.isOpened():
        raise RuntimeError(f"Cannot open camera {args.camera}")
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # always show the newest frame

    last_publish = 0.0
    publisher = LivePublisher(LIVE_URL)
    last_recognize = 0.0
    fps, last_frame = 0.0, time.monotonic()
    seen_version = None   # server's belongings_version we last loaded for
    guide = AngleCapture()
    enrolling = False
    enroll_id = None

    print("RecallAI started - press Q to quit")
    print(f"Dashboard mirror: {LIVE_URL}")
    try:
        while True:
            ok, frame = camera.read()
            if not ok:
                break

            results = model(frame, conf=DETECT_CONF, verbose=False)[0]
            detections = to_detections(results, model.names)
            mine = [(m, live_box(det.xyxy, detections))
                    for m, det in list(worker.found.values())]
            display = frame.copy()
            strip = 0   # height of the enrollment prompt along the bottom, if any
            draw_detections(display, detections, skip=[box for _, box in mine])
            now = time.monotonic()

            # People named on the dashboard since the last frame: save a
            # photo of each so the worker recognizes them from now on.
            for job in publisher.take_people_jobs():
                try:
                    saved = teach_person(frame, detections, job)
                except Exception as e:  # keep the camera running
                    print(f"[people] could not save a photo ({e})")
                    continue
                if saved is None:
                    print("[people] nobody is at that spot any more; skipped")
                else:
                    worker.request_people_reload()
                    print(f"[people] saved a photo of person {job.get('person_id')}")

            # A belonging was deleted on the dashboard: stop tracking it.
            version = getattr(publisher, "belongings_version", None)
            if version is not None and version != seen_version:
                if seen_version is not None:
                    worker.request_reload()
                    print("[recognition] belongings changed on the dashboard, reloading")
                seen_version = version

            remote = publisher.remote_enroll or {}
            if remote.get("active"):
                if not enrolling:
                    guide.reset()
                    enroll_id = None
                    enrolling = True
                outcome = guide.step(detections, now)
                if outcome.save and outcome.box is not None:
                    crop = crop_box(frame, outcome.box)
                    if crop is None or not crop.size:
                        guide.fail_shot(
                            "Couldn't grab that view. Hold it still and try again.")
                    else:
                        try:
                            # The dashboard user who pressed Add owns it.
                            info, count = save_angle_default(
                                outcome.category, crop, enroll_id,
                                remote.get("owner_id"))
                        except Exception as e:
                            print(f"[enroll] save failed: {e}")
                            guide.fail_shot(
                                "Couldn't save that angle. Hold it still and try again.")
                        else:
                            enroll_id = info["id"]
                            guide.note_saved(
                                info["category"], count, outcome.box,
                                info["name"], info["id"], now)
                            print(f"[enroll] {info['name']}: {count} angle(s)")
                            if info.get("tracking"):
                                worker.request_reload()
                if outcome.box:
                    draw_corners(display, outcome.box, (0, 220, 255), 2)
                strip = _paint_enroll(display, guide.message)
            else:
                enrolling = False

            # Hand the frame to the background worker; never wait for it.
            if now - last_recognize >= RECOGNIZE_EVERY and worker.idle:
                worker.submit(frame, detections, gps.latest, now)
                last_recognize = now

            # Highlight your belongings in green with their name
            for m, box in mine:
                draw_corners(display, box, (0, 255, 0), 3)
                cv2.putText(display, f"{m.name} {m.score:.0%}",
                            (int(box[0]), max(20, int(box[1]) - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            dt = now - last_frame
            last_frame = now
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1 / dt) if fps else 1 / dt
            _paint_fps(display, fps, strip)
            cv2.imshow("RecallAI - Visual Memory", display)

            if now - last_publish >= PUBLISH_EVERY:
                in_view = [{
                    "belonging_id": m.belonging_id,
                    "name": m.name,
                    "category": m.category,
                    "score": round(m.score, 3),
                    "bbox": [int(v) for v in box],
                } for m, box in mine]
                # Names stay off the shared frame: the server shows each
                # account only its own people.
                persons = [d for d in detections if d.category == PERSON]
                named = [{
                    "person_id": pm.person_id,
                    "name": pm.name,
                    "score": round(pm.score, 3),
                    "bbox": [int(v) for v in live_box(det.xyxy, persons)],
                } for pm, det in list(worker.people_found)]
                publisher.submit(display, {
                    "gps": gps.latest is not None,
                    "in_view": in_view,
                    "people": named,
                    "detections": [
                        {
                            "category": d.category,
                            "confidence": round(d.confidence, 3),
                            "bbox": [int(v) for v in d.xyxy],
                        }
                        for d in detections[:30]
                    ],
                }, enroll=guide.status() if enrolling else None)
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
