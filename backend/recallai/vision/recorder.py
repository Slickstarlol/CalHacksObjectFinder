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

Ownership: the camera sees every account's belongings. Each sighting is
written with the owner_id of the belonging it matched (re-read from
info.json every few seconds, so an item claimed after the camera started is
tagged correctly), and with THAT owner's phone GPS when `gps` is a
per-owner map ({"by_owner": {"<user id>": fix}}).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import time

import cv2

from recallai import paths
from recallai.memory import db as dbmod
from recallai.vision.recognition import crop_box, read_owner

OWNER_TTL = 5.0   # seconds to cache a belonging's owner between info.json reads


def gps_for_owner(gps, owner_id):
    """Pick the fix to attach to an owner's sighting.

    gps is None, a single fix (has "latitude": shared, single-user setups),
    or {"by_owner": {"<owner id>": fix}} from the server's /api/device/gps."""
    if not gps:
        return None
    if "latitude" in gps:
        return gps
    by_owner = gps.get("by_owner") or {}
    if owner_id is None:
        return None
    return by_owner.get(str(owner_id))

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
    owner_id: int | None = None


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
        self._owners = {}        # belonging_id -> (owner_id, monotonic time read)

    def close(self):
        self.db.close()

    def owner_of(self, bid, match=None, now=None):
        """Current owner of a belonging (info.json, cached OWNER_TTL s)."""
        now = time.monotonic() if now is None else now
        cached = self._owners.get(bid)
        if cached and now - cached[1] < OWNER_TTL:
            return cached[0]
        base = getattr(self.matcher, "base_dir", None)
        owner = read_owner(Path(base) / bid) if base is not None else None
        if owner is None and match is not None:
            owner = getattr(match, "owner_id", None)
        self._owners[bid] = (owner, now)
        return owner

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
            owner = self.owner_of(bid, m, now)
            self.last_seen[bid] = _Seen(frame.copy(), crop.copy(), det,
                                        m.score, nearby, gps_for_owner(gps, owner),
                                        timestamp, now, owner)
            self.left_logged.discard(bid)

            if now - self.last_saved.get(bid, -1e12) >= self.save_interval:
                row_id = self._write(bid, m.name, self.last_seen[bid], "seen")
                self.last_saved[bid] = now
                self.last_row[bid] = (row_id, now)

        # Items that were visible but haven't been for a while.
        registered = {b.id for b in self.matcher.belongings}
        for bid, seen in list(self.last_seen.items()):
            if bid not in registered:
                # Deleted on the dashboard: forget it, don't log it leaving.
                for state in (self.last_seen, self.last_saved, self.last_row):
                    state.pop(bid, None)
                self.left_logged.discard(bid)
                continue
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
            owner_id=seen.owner_id,
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
