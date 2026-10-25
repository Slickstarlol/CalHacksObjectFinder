"""Collect three still angles of one object from the live camera.

webcam.py owns the camera, so it drives this. The dashboard only starts
the session and shows the prompt. YOLO's category becomes the name — there
is nothing to type. A shot is kept once it is saved. A later frame that
misses the object, or labels it as something else, does not erase those
shots; it asks for that angle again.

The same YOLO category has to hold for all three. Confusable classes
(a mouse YOLO sometimes calls a cell phone) are a failure here on purpose:
the three photos are how the tracker learns the object, so they must be
the same label.

Ownership: the dashboard user who pressed Add owns the session; the server
hands their user id to the camera with the enroll status, and the new
belonging's info.json gets that "owner_id". Unfinished items and name
de-duplication ("cell phone 2") are per owner.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from recallai.vision.recognition import BELONGINGS_DIR, load_belongings
from recallai.vision.register import save_reference

ANGLES = 3
STILL_FOR = 0.75     # seconds the box must barely move
TURN_FOR = 1.4       # after a save, wait this long before the next still-shot
MISS_FOR = 0.4       # a one-frame YOLO blip is not a failed angle
STILL_IOU = 0.80
# A person in the background is usually the biggest box. They are not a belonging.
IGNORED_CATEGORIES = {"person"}


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = ((a[2] - a[0]) * (a[3] - a[1])
             + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / union if union > 0 else 0.0


def _area(det):
    x1, y1, x2, y2 = det.xyxy
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


@dataclass
class Step:
    save: bool
    box: tuple | None
    category: str | None = None


class AngleCapture:
    """Frame-by-frame decision for one enrollment. Disk I/O stays outside."""

    def __init__(self, needed=ANGLES, still_for=STILL_FOR, turn_for=TURN_FOR,
                 miss_for=MISS_FOR, still_iou=STILL_IOU):
        self.needed = needed
        self.still_for = still_for
        self.turn_for = turn_for
        self.miss_for = miss_for
        self.still_iou = still_iou
        self.reset()

    def reset(self):
        self.category = None
        self.name = None
        self.belonging_id = None
        self.saved = 0
        self.phase = "hold"
        self.message = "Show one object and hold it still."
        self.hold_box = None
        self.hold_since = None
        self.hold_category = None
        self.cooldown_until = None
        self._trouble_since = None

    def status(self):
        return {
            "phase": self.phase,
            "message": self.message,
            "category": self.category,
            "name": self.name,
            "saved": self.saved,
            "belonging_id": self.belonging_id,
        }

    def fail_shot(self, message):
        """A grab failed. The angles already on disk stay saved."""
        self.hold_box = None
        self.hold_since = None
        self.hold_category = None
        self.phase = "retry"
        self.message = message

    def note_saved(self, category, saved, box, name, belonging_id, now):
        self.category = category
        self.name = name
        self.belonging_id = belonging_id
        self.saved = saved
        self.hold_box = None
        self.hold_since = None
        self.hold_category = None
        self._trouble_since = None
        if saved >= self.needed:
            self.cooldown_until = None
            self.phase = "done"
            self.message = f"{name} is in the tracker."
            return
        self.cooldown_until = now + self.turn_for
        self.phase = "turn"
        self.message = (f"Got that angle. Turn the {category} for the next one. "
                        f"{saved} of {self.needed} saved.")

    def step(self, detections, now):
        if self.saved >= self.needed:
            self.phase = "done"
            self.message = f"{self.name or self.category} is in the tracker."
            return Step(False, None)

        usable = [d for d in detections if d.category not in IGNORED_CATEGORIES]
        if not usable:
            # Only people (or nothing) in frame. That is a miss, not a new object.
            kind = "people" if detections and self.category is None else "missing"
            return self._trouble(kind, None, now)

        det, kind = self._pick(usable)
        if kind != "ok":
            return self._trouble(kind, det, now)

        self._trouble_since = None
        box = tuple(float(v) for v in det.xyxy)

        if self.cooldown_until is not None and now < self.cooldown_until:
            self.phase = "turn"
            self.message = (f"Turn the {self.category} to a new angle. "
                            f"{self.saved} of {self.needed} saved.")
            return Step(False, box)

        if self.hold_box is None or _iou(box, self.hold_box) < self.still_iou:
            self.hold_box = box
            self.hold_since = now
            self.hold_category = det.category
            self.phase = "hold"
            self.message = self._hold_message(det.category)
            return Step(False, box)

        if now - self.hold_since < self.still_for:
            self.phase = "hold"
            self.message = self._hold_message(det.category)
            return Step(False, box)

        return Step(True, box, det.category)

    def _hold_message(self, category):
        label = self.category or category
        if self.saved:
            return f"Hold the {label} still. {self.saved} of {self.needed} saved."
        return f"Hold the {label} still."

    def _pick(self, detections):
        """The box to follow, and whether it is still the same object.

        'ok' is the locked category, or (before the first save) the largest
        box. 'different' means YOLO is calling it something else. 'changed'
        means the label flipped before the first save. 'missing' means no box.
        """
        if not detections:
            return None, "missing"
        if self.category:
            same = [d for d in detections if d.category == self.category]
            if same:
                return max(same, key=_area), "ok"
            return max(detections, key=_area), "different"
        best = max(detections, key=_area)
        if self.hold_category and best.category != self.hold_category:
            return best, "changed"
        return best, "ok"

    def _trouble(self, kind, det, now):
        if self._trouble_since is None:
            self._trouble_since = now
        if now - self._trouble_since < self.miss_for:
            return Step(False, self.hold_box)
        self.hold_box = None
        self.hold_since = None
        self.hold_category = None
        self.phase = "retry"
        if kind == "people":
            self.message = "People don't count. Hold the object you want in view."
        elif kind == "missing":
            label = self.category or "it"
            self.message = f"I don't see {label}. Hold it in view and try again."
        elif kind == "different":
            other = det.category
            self.message = (f"That looks like a {other}, not a {self.category}. "
                            f"Show the {self.category} again.")
        else:
            seen = det.category if det is not None else "something else"
            self.message = f"That changed to a {seen}. Hold one object still."
        return Step(False, None)


def _write_info(folder, info):
    (folder / "info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")


def _mine(base_dir, owner_id):
    return [b for b in load_belongings(base_dir) if b.owner_id == owner_id]


def _unique_name(base_dir, category, owner_id=None):
    taken = {b.name.lower() for b in _mine(base_dir, owner_id)}
    if category.lower() not in taken:
        return category
    n = 2
    while f"{category} {n}".lower() in taken:
        n += 1
    return f"{category} {n}"


def _unfinished(base_dir, category, owner_id=None):
    """This owner's in-progress item of this category with the most angles kept."""
    items = [b for b in _mine(base_dir, owner_id)
             if b.category == category and not b.tracking]
    if not items:
        return None
    return max(items, key=lambda b: len(b.reference_paths))


def _begin(base_dir, category, owner_id=None):
    name = _unique_name(base_dir, category, owner_id)
    item_id = uuid.uuid4().hex[:12]
    folder = Path(base_dir) / item_id
    folder.mkdir(parents=True)
    info = {"id": item_id, "name": name, "category": category, "tracking": False}
    if owner_id is not None:
        info["owner_id"] = owner_id
    _write_info(folder, info)
    return folder, info


def save_angle(base_dir, category, crop, belonging_id=None, owner_id=None):
    """Append one reference crop. Photos already saved are left in place.

    owner_id: the account enrolling it (None = ownerless, claimed later).
    Returns (info, count). info['tracking'] is true once `ANGLES` photos exist.
    """
    base_dir = Path(base_dir)
    owner_id = None if owner_id is None else int(owner_id)
    folder = info = None
    if belonging_id:
        folder = base_dir / belonging_id
        info_path = folder / "info.json"
        if info_path.is_file():
            info = json.loads(info_path.read_text(encoding="utf-8"))
            if (info.get("category") != category or info.get("tracking")
                    or info.get("owner_id") != owner_id):
                folder = info = None
    if info is None:
        existing = _unfinished(base_dir, category, owner_id)
        if existing is not None:
            folder = existing.folder
            info = json.loads((folder / "info.json").read_text(encoding="utf-8"))
        else:
            folder, info = _begin(base_dir, category, owner_id)
    save_reference(folder, crop)
    count = len(list(folder.glob("reference_*.jpg")))
    if count >= ANGLES:
        info["tracking"] = True
        _write_info(folder, info)
    return info, count


def save_angle_default(category, crop, belonging_id=None, owner_id=None):
    return save_angle(BELONGINGS_DIR, category, crop, belonging_id, owner_id)
