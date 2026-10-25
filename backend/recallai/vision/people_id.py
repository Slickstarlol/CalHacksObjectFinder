"""Put names on the people the camera sees.

YOLO only says "person". When a dashboard user double-clicks a person on the
live feed and types a name, the server queues that box (POST /api/live/people)
and the camera saves a crop of them as a reference photo in
data/people/<person id>/. From then on PeopleMatcher compares every person box
with those photos, using the same embeddings as belongings (DINOv2), and the
dashboard shows the name. Every extra label adds a photo, so it gets better
the more you name someone.

Names and owners come from the SQLite people table (memory/people.py), so a
rename shows up without new photos, and the folder of a deleted person is
ignored. Whole-body crops mostly see clothes: this recognizes someone for the
day, not for ever.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import cv2
import numpy as np

from recallai import paths
from recallai.memory import db as dbmod
from recallai.vision.recognition import IMAGE_SUFFIXES, box_iou, crop_box

PERSON = "person"           # the YOLO category this module names
MAX_REFERENCES = 12         # newest photos kept per person
SNAP_IOU = 0.2              # a label lands on the person box it overlaps this much
# Person boxes look far more alike than a laptop and a bottle, so the bar is
# higher than for belongings. Raise it (--person-threshold) if a stranger
# gets someone's name.
THRESHOLDS = {"dinov2": 0.75, "clip": 0.90, "histogram": 0.85}


def person_folder(person_id, base_dir=None):
    return Path(base_dir or paths.PEOPLE_DIR) / str(int(person_id))


def _references(folder):
    try:
        return sorted(p for p in Path(folder).iterdir()
                      if p.suffix.lower() in IMAGE_SUFFIXES and p.stem.startswith("reference"))
    except OSError:
        return []


def photos_of(person_id, base_dir=None):
    """The camera's photos of a person, newest first."""
    return _references(person_folder(person_id, base_dir))[::-1]


def headshot_jpeg(path, size=160):
    """A square head-and-shoulders JPEG from a whole-body photo: the top
    width x width of it (the centre square if the photo is wider than tall),
    scaled to `size`. None if the file can't be read."""
    img = cv2.imread(str(path))
    if img is None or not img.size:
        return None
    h, w = img.shape[:2]
    side = min(w, h)
    x = (w - side) // 2
    square = img[0:side, x:x + side]
    square = cv2.resize(square, (size, size), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", square, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return buf.tobytes() if ok else None


def save_reference(person_id, crop_bgr, base_dir=None, keep=MAX_REFERENCES):
    """Store one crop of a person and drop the oldest beyond `keep`."""
    folder = person_folder(person_id, base_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"reference_{time.time_ns()}.jpg"
    if not cv2.imwrite(str(path), crop_bgr):
        raise OSError(f"could not write {path}")
    for old in _references(folder)[:-keep]:
        old.unlink(missing_ok=True)
    return path


def teach(frame, detections, job, base_dir=None):
    """Save the person a dashboard user named. `job` comes from the server:
    {"person_id", "bbox": preview pixels, "width": preview width}. The box is
    moved onto this frame's person detection that overlaps it most, so a
    small step between the click and now doesn't matter.

    Returns the saved path, or None when nobody is at that spot any more."""
    h, w = frame.shape[:2]
    scale = w / float(job.get("width") or w)
    want = [float(v) * scale for v in job["bbox"]]
    people = [d for d in detections if d.category == PERSON]
    best = max(people, key=lambda d: box_iou(want, d.xyxy), default=None)
    if best is None or box_iou(want, best.xyxy) < SNAP_IOU:
        return None
    crop = crop_box(frame, best.xyxy)
    if crop is None or not crop.size:
        return None
    return save_reference(job["person_id"], crop, base_dir)


@dataclass
class PersonMatch:
    person_id: int
    name: str
    score: float
    owner_id: int | None = None


@dataclass
class _Known:
    person_id: int
    name: str
    owner_id: int | None
    refs: np.ndarray            # (N, D) embeddings of their reference photos


class PeopleMatcher:
    """Reference embeddings for every person who has been named.

    Call reload() from the thread that runs the embedder (the camera's
    recognition worker); it is cheap when nothing changed, because each
    photo is embedded once and kept in memory.
    """

    def __init__(self, embedder, base_dir=None, db_path=None, threshold=None, top_k=2):
        self.embedder = embedder
        self.base_dir = base_dir
        self.db_path = db_path
        self.threshold = (threshold if threshold is not None
                          else THRESHOLDS.get(getattr(embedder, "name", ""), 0.80))
        self.top_k = top_k
        self.known = []
        self._vectors = {}       # "path:mtime" -> embedding
        self._db_ready = False

    def _people_rows(self):
        if not self._db_ready:
            dbmod.init_db(self.db_path)
            self._db_ready = True
        with dbmod.connect(self.db_path) as db:
            rows = db.execute("SELECT id, name, owner_id FROM people").fetchall()
        return {int(r["id"]): (r["name"], r["owner_id"]) for r in rows}

    def reload(self):
        """Re-read names (a rename changes them) and any new photos."""
        known, seen = [], set()
        for pid, (name, owner) in self._people_rows().items():
            refs = _references(person_folder(pid, self.base_dir))
            emb = self._embeddings(refs, seen)
            if len(emb):
                known.append(_Known(pid, name, owner, emb))
        self._vectors = {k: v for k, v in self._vectors.items() if k in seen}
        self.known = known
        return self

    def _embeddings(self, refs, seen):
        keys = {}
        for p in refs:
            try:
                keys[f"{p}:{p.stat().st_mtime_ns}"] = p
            except OSError:      # pruned by save_reference meanwhile
                continue
        seen.update(keys)
        new = []
        for key, p in keys.items():
            if key not in self._vectors:
                img = cv2.imread(str(p))
                if img is not None and img.size:
                    new.append((key, img))
        if new:
            for (key, _), vec in zip(new, self.embedder.embed([img for _, img in new])):
                self._vectors[key] = vec
        vecs = [self._vectors[k] for k in keys if k in self._vectors]
        return np.stack(vecs) if vecs else np.zeros((0, 0), np.float32)

    def match(self, frame, detections):
        """[(PersonMatch, Detection)] for the person boxes it recognizes.

        Each account's people are assigned on their own: within one account a
        name goes to at most one box and a box gets at most one name, best
        scores first."""
        if not self.known:
            return []
        crops, dets = [], []
        for d in detections:
            if d.category != PERSON:
                continue
            crop = crop_box(frame, d.xyxy)
            if crop is not None and crop.size:
                crops.append(crop)
                dets.append(d)
        if not crops:
            return []
        emb = self.embedder.embed(crops)
        candidates = []
        for k in self.known:
            sims = emb @ k.refs.T                   # (boxes, photos)
            top = min(self.top_k, sims.shape[1])
            # Mean of the best photos: one odd photo can't name a stranger.
            scores = np.sort(sims, axis=1)[:, -top:].mean(axis=1)
            candidates += [(float(s), i, k) for i, s in enumerate(scores) if s >= self.threshold]
        candidates.sort(key=lambda c: -c[0])
        boxes_taken, names_taken, found = set(), set(), []
        for score, i, k in candidates:
            if (k.owner_id, i) in boxes_taken or k.person_id in names_taken:
                continue
            boxes_taken.add((k.owner_id, i))
            names_taken.add(k.person_id)
            found.append((PersonMatch(k.person_id, k.name, score, k.owner_id), dets[i]))
        return found
