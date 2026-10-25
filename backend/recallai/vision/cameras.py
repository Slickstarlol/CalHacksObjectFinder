"""Which cameras can RecallAI use? The launcher's Camera step lists them.

    python -m recallai.vision.cameras                # readable list
    python -m recallai.vision.cameras --json         # [{"index": 0, "width": 1280, "height": 720}]
    python -m recallai.vision.cameras --skip 0       # don't touch camera 0 (in use)

The index is what `python -m recallai.vision.webcam --camera N` takes. A
camera another program is using may not open, so it can be missing here.
"""

import argparse
import json
import os

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")   # no driver noise for missing indices

import cv2  # noqa: E402

MAX_INDEX = 5


def probe(max_index=MAX_INDEX, skip=()):
    """[{"index", "width", "height"}] for every camera that delivers a frame."""
    backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY   # same as webcam.py
    found = []
    for i in range(max_index):
        if i in skip:
            continue
        cap = cv2.VideoCapture(i, backend)
        try:
            if not cap.isOpened():
                continue
            ok, frame = cap.read()
            if ok and frame is not None:
                h, w = frame.shape[:2]
                found.append({"index": i, "width": int(w), "height": int(h)})
        finally:
            cap.release()
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max", type=int, default=MAX_INDEX, help="indices to try (0..max-1)")
    ap.add_argument("--skip", type=int, action="append", default=[],
                    help="an index not to open (e.g. the one the camera is using)")
    args = ap.parse_args(argv)
    found = probe(args.max, set(args.skip))
    if args.json:
        print(json.dumps(found))
    elif not found:
        print("No cameras found.")
    else:
        for c in found:
            print(f"Camera {c['index']}: {c['width']}x{c['height']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
