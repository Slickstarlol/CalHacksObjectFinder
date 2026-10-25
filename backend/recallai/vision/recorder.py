"""Decide what to remember from each camera frame.

Kept separate from webcam.py so it can be tested without a camera:
feed it a frame plus YOLO detections and it writes sightings.

Two kinds of memory are written per belonging:
  * 'seen'      - periodic snapshot while the item is in view
                  (at most once every SAVE_INTERVAL seconds)
  * 'left_view' - the LAST frame the item was visible in, written once
                  the item has been out of view for GONE_AFTER seconds.
                  This is usually the moment you put it down and walked
                  away, which is the most useful memory of all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import time

import cv2

from recallai import paths
from recallai.memory import db as dbmod
from recallai.vision.recognition import crop_box

SNAPSHOT_DIR = paths.SNAPSHOT_DIR   # data/snapshots; SQLite stores "snapshots/<file>"


@dataclass
class Detection:
    category: str
    confidence: float
    xyxy: tuple  # (x1, y1, x2, y2)


@dataclass
class _Seen:
    frame: object
    crop: object
    det: Detection
    score: float
    nearby: str
    gps: dict | None
    timestamp: str
    mono: float


class SightingRecorder:
    def __init__(self, matcher, db_path=None, snapshot_dir=SNAPSHOT_DIR,
                 save_interval=15.0, gone_after=5.0, on_saved=None):
        self.matcher = matcher
        self.db_path = db_path
        self.snapshot_dir = Path(snapshot_dir)
        (self.snapshot_dir / "crops").mkdir(parents=True, exist_ok=True)
        self.save_interval = save_interval
        self.gone_after = gone_after
        self.on_saved = on_saved  # e.g. Interpreter.submit(row_id) -> Grok
        dbmod.init_db(db_path)
        self.db = dbmod.connect(db_path)
        self.last_saved = {}     # belonging_id -> monotonic time
        self.last_seen = {}      # belonging_id -> _Seen
        self.left_logged = set() # belonging_ids whose departure is recorded
        self.last_row = {}       # belonging_id -> (row id, monotonic time) of last 'seen'

    def close(self):
        self.db.close()

    # ------------------------------------------------------------------
    def process(self, frame, detections, gps=None, now=None):
        """Handle one frame. Returns {belonging_id: (Match, Detection)}."""
        now = time.monotonic() if now is None else now
        timestamp = datetime.now(timezone.utc).isoformat()

        relevant = [d for d in detections
                    if d.category in self.matcher.categories]
        crops = [crop_box(frame, d.xyxy) for d in relevant]
        matches = self.matcher.match_many(crops, [d.category for d in relevant])

        # If two detections both look like "My Bag", keep the best one.
        best = {}
        for det, crop, m in zip(relevant, crops, matches):
            if m and (m.belonging_id not in best
                      or m.score > best[m.belonging_id][2].score):
                best[m.belonging_id] = (det, crop, m)

        for bid, (det, crop, m) in best.items():
            # Other objects around it. Boxes overlapping the item itself are
            # YOLO giving it a second label (mouse -> "cell phone"), not
            # neighbours, so skip them.
            nearby = ", ".join(sorted({
                d.category for d in detections
                if d.category != det.category and _iou(d.xyxy, det.xyxy) < 0.5
            }))
            self.last_seen[bid] = _Seen(frame.copy(), crop.copy(), det,
                                        m.score, nearby, gps, timestamp, now)
            self.left_logged.discard(bid)

            if now - self.last_saved.get(bid, -1e12) >= self.save_interval:
                row_id = self._write(bid, m.name, self.last_seen[bid], "seen")
                self.last_saved[bid] = now
                self.last_row[bid] = (row_id, now)

        # Items that were visible but haven't been for a while.
        for bid, seen in list(self.last_seen.items()):
            if bid in best or bid in self.left_logged:
                continue
            if now - seen.mono >= self.gone_after:
                row_id, saved_at = self.last_row.get(bid, (None, None))
                if saved_at == seen.mono:
                    # The last 'seen' row IS this final frame: relabel it
                    # instead of storing the same snapshot twice.
                    self.db.execute("UPDATE sightings SET event='left_view' "
                                    "WHERE id=?", (row_id,))
                    self.db.commit()
                    print(f"[LEFT_VIEW] {bid} (relabelled last sighting)")
                else:
                    name = next((b.name for b in self.matcher.belongings
                                 if b.id == bid), bid)
                    self._write(bid, name, seen, "left_view")
                self.left_logged.add(bid)

        return {bid: (m, det) for bid, (det, _, m) in best.items()}

    # ------------------------------------------------------------------
    def _write(self, bid, name, seen, event):
        stamp = int(time.time() * 1000)
        slug = f"{stamp}_{bid}_{event}"
        frame_rel = f"snapshots/{slug}.jpg"
        crop_rel = f"snapshots/crops/{slug}.jpg"
        cv2.imwrite(str(self.snapshot_dir / f"{slug}.jpg"), seen.frame)
        cv2.imwrite(str(self.snapshot_dir / "crops" / f"{slug}.jpg"), seen.crop)

        gps = seen.gps or {}
        x1, y1, x2, y2 = (int(v) for v in seen.det.xyxy)
        row_id = dbmod.insert_sighting(
            self.db,
            object_name=seen.det.category,
            latitude=gps.get("latitude"),
            longitude=gps.get("longitude"),
            accuracy=gps.get("accuracy"),
            timestamp=seen.timestamp,
            image_path=frame_rel,
            belonging_id=bid,
            belonging_name=name,
            event=event,
            confidence=round(seen.det.confidence, 3),
            match_score=round(seen.score, 3),
            bbox=f"{x1},{y1},{x2},{y2}",
            crop_path=crop_rel,
            nearby=seen.nearby,
        )
        print(f"[{event.upper()}] {name} (match {seen.score:.2f})"
              + (f" near: {seen.nearby}" if seen.nearby else ""))
        if self.on_saved:
            self.on_saved(row_id)
        return row_id


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1])
             - inter)
    return inter / union if union > 0 else 0.0
