"""Grok looks at each saved sighting and records:

  * verified     - is the boxed object really YOUR item? (second opinion
                   on top of DINOv2; rejected sightings are hidden)
  * description  - one sentence on where it is, e.g. "On the gray folding
                   table, to the left of a green bag and a water bottle."
  * place        - short reusable label, e.g. "hackathon table"

Runs in a background thread so the camera never waits for the API.

Backfill sightings saved before Grok was enabled:
    python -m recallai.ai.interpreter --backfill
"""

from __future__ import annotations

import queue
import threading

import cv2

from recallai.memory import db as dbmod
from recallai.ai.llm import (VISION_MODEL, DATA_DIR, call_tool, function_tool,
                 get_client, image_part, text_part)
from recallai.vision.recognition import BELONGINGS_DIR, load_belongings

TOOL = function_tool(
    "record_sighting",
    "Record your interpretation of this sighting.",
    {
        "type": "object",
        "properties": {
            "same_item": {
                "type": "string", "enum": ["yes", "no", "unsure"],
                "description": "Is the object in the GREEN box the same "
                               "physical item as in the reference photos?",
            },
            "verify_reason": {
                "type": "string",
                "description": "Short reason (distinguishing features).",
            },
            "location": {
                "type": "string",
                "description": "One sentence telling the owner where the item "
                               "is, using visible landmarks (surface, furniture, "
                               "nearby objects, left/right of what). No GPS.",
            },
            "place": {
                "type": "string",
                "description": "2-4 word reusable place label, e.g. "
                               "'hackathon table', 'desk by window', 'car seat'.",
            },
        },
        "required": ["same_item", "verify_reason", "location", "place"],
    },
)

PROMPT = """You are the visual memory of RecallAI, which helps {owner} remember where their belongings are.

Image 1 (and 2 if present): reference photos of {owner}'s "{name}".
Last image: a camera frame. A GREEN box marks the object the camera thinks is "{name}".

1. Decide if the boxed object is the same physical item as the references (not just the same kind of object). Say "unsure" if the view is too blurry or small to tell.
2. Describe where it is so {owner} could walk over and pick it up.
Call record_sighting."""


def frame_with_box(image_path, bbox):
    frame = cv2.imread(str(DATA_DIR / image_path))
    if frame is None:
        raise FileNotFoundError(image_path)
    if bbox:
        x1, y1, x2, y2 = (int(v) for v in bbox.split(","))
        thick = max(2, frame.shape[1] // 200)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), thick)
    return frame


def interpret(client, row, belongings_dir=BELONGINGS_DIR, owner="the owner",
              model=VISION_MODEL):
    """One Grok call for one sighting row. Returns the tool arguments."""
    item = next((b for b in load_belongings(belongings_dir)
                 if b.id == row["belonging_id"]), None)
    content = []
    if item:
        for ref in item.reference_paths[:2]:
            content.append(image_part(ref, max_side=384))
    content.append(image_part(frame_with_box(row["image_path"], row["bbox"])))
    content.append(text_part(PROMPT.format(owner=owner, name=row["belonging_name"])))
    return call_tool(client, model, content, TOOL)


def save_interpretation(db, sighting_id, result):
    verified = {"yes": 1, "no": 0}.get(result.get("same_item"))
    db.execute(
        "UPDATE sightings SET verified=?, verify_reason=?, description=?, place=? "
        "WHERE id=?",
        (verified, result.get("verify_reason"), result.get("location"),
         result.get("place"), sighting_id),
    )
    db.commit()


class Interpreter:
    """Background worker: recorder calls submit(row_id) after each save."""

    def __init__(self, client=None, db_path=None, belongings_dir=BELONGINGS_DIR,
                 owner="the owner"):
        self.client = client or get_client()
        self.db_path = db_path
        self.belongings_dir = belongings_dir
        self.owner = owner
        self.q = queue.Queue()
        self.enabled = self.client is not None
        if self.enabled:
            threading.Thread(target=self._run, daemon=True).start()

    def submit(self, sighting_id):
        if self.enabled:
            self.q.put(sighting_id)

    def process(self, sighting_id):
        """Interpret one sighting synchronously (used by the worker + tests)."""
        with dbmod.connect(self.db_path) as db:
            row = db.execute("SELECT * FROM sightings WHERE id=?",
                             (sighting_id,)).fetchone()
            if row is None or not row["belonging_id"]:
                return None
            result = interpret(self.client, row, self.belongings_dir, self.owner)
            save_interpretation(db, sighting_id, result)
        tag = {"yes": "confirmed", "no": "REJECTED"}.get(
            result.get("same_item"), "unsure")
        print(f"[GROK] #{sighting_id} {row['belonging_name']} {tag}: "
              f"{result.get('location')}")
        return result

    def _run(self):
        while True:
            sid = self.q.get()
            try:
                self.process(sid)
            except Exception as e:  # never kill the worker
                print(f"[GROK] could not interpret #{sid}: {e}")
            finally:
                self.q.task_done()

    def wait(self):
        self.q.join()


def backfill(client=None, db_path=None):
    it = Interpreter(client=client, db_path=db_path)
    if not it.enabled:
        raise SystemExit("Set XAI_API_KEY first.")
    dbmod.init_db(db_path)
    with dbmod.connect(db_path) as db:
        ids = [r[0] for r in db.execute(
            "SELECT id FROM sightings WHERE belonging_id IS NOT NULL "
            "AND description IS NULL ORDER BY id")]
    print(f"Interpreting {len(ids)} sightings...")
    for sid in ids:
        it.submit(sid)
    it.wait()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    if ap.parse_args().backfill:
        backfill()
    else:
        ap.print_help()
