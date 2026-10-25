"""Push the annotated camera frame to the RecallAI server.

webcam.py draws boxes, then hands the frame here. A background thread posts
the latest JPEG to POST /api/live so the React dashboard can show it.

POST /api/live is device-only. On the same machine nothing is needed (the
server trusts loopback requests without a browser Origin). For a camera on
another machine, set the same RECALLAI_DEVICE_KEY for the server and the
camera; it is sent as the X-RecallAI-Device-Key header.
"""

import base64
import os
import threading

import cv2
import requests

SERVER_URL = os.environ.get("RECALLAI_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
LIVE_URL = SERVER_URL + "/api/live"
DEVICE_KEY_HEADER = "X-RecallAI-Device-Key"


def device_headers():
    """Headers proving this process is the camera (empty on the same machine)."""
    key = os.environ.get("RECALLAI_DEVICE_KEY", "").strip()
    return {DEVICE_KEY_HEADER: key} if key else {}
PUBLISH_EVERY = 0.12
PREVIEW_EDGE = 960


def fit_preview(frame, max_edge=PREVIEW_EDGE):
    """Shrink a frame so the dashboard payload stays small. Returns (image, scale)."""
    h, w = frame.shape[:2]
    edge = max(h, w)
    if edge <= max_edge or w == 0:
        return frame, 1.0
    scale = max_edge / edge
    resized = cv2.resize(
        frame,
        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, resized.shape[1] / float(w)


def scale_track(item, scale):
    out = dict(item)
    box = out.get("bbox")
    if box:
        out["bbox"] = [int(round(float(v) * scale)) for v in box]
    return out


class LivePublisher:
    """Send the latest annotated frame off the camera thread."""

    def __init__(self, url=LIVE_URL, interval=PUBLISH_EVERY):
        self.url = url
        self.interval = interval
        self._lock = threading.Lock()
        self._pending = None
        self._stop = threading.Event()
        self.remote_enroll = None
        self._thread = threading.Thread(
            target=self._loop, name="recall-live", daemon=True)
        self._thread.start()

    def submit(self, frame_bgr, meta, enroll=None):
        try:
            preview, scale = fit_preview(frame_bgr)
            ok, buf = cv2.imencode(
                ".jpg", preview, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
            if not ok:
                return
            payload = {
                "gps": bool(meta.get("gps")),
                "width": int(preview.shape[1]),
                "height": int(preview.shape[0]),
                "in_view": [scale_track(i, scale) for i in meta.get("in_view", [])],
                "detections": [scale_track(i, scale) for i in meta.get("detections", [])],
            }
            if enroll is not None:
                payload["enroll"] = enroll
            with self._lock:
                self._pending = (buf.tobytes(), payload)
        except Exception as e:
            print(f"[live] could not encode frame ({e})")

    def close(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self.interval):
            with self._lock:
                item = self._pending
                self._pending = None
            if item is None:
                continue
            jpeg, payload = item
            body = {
                "frame_jpeg_b64": base64.b64encode(jpeg).decode("ascii"),
                **payload,
            }
            try:
                kwargs = {"json": body, "timeout": 0.5}
                headers = device_headers()
                if headers:
                    kwargs["headers"] = headers
                response = requests.post(self.url, **kwargs)
            except requests.RequestException:
                continue
            try:
                data = response.json()
            except Exception:
                data = None
            if isinstance(data, dict) and "enroll" in data:
                self.remote_enroll = data["enroll"]
