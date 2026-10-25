"""Shared database setup for RecallAI.

Every script imports this so they all agree on where memory.db lives and
what the sightings table looks like. Older databases (from the first
version of webcam.py) are upgraded in place without losing rows.
"""

from pathlib import Path
import sqlite3

from recallai import paths

DB_PATH = paths.DB_PATH   # data/memory.db (RECALLAI_DB_PATH overrides)

# Final schema. GPS is optional: indoors it is too coarse to matter,
# so a sighting is still worth saving without it.
SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_name TEXT NOT NULL,          -- YOLO category, e.g. "backpack"
    latitude REAL,
    longitude REAL,
    accuracy REAL,
    timestamp TEXT NOT NULL,            -- ISO 8601, UTC
    image_path TEXT NOT NULL,           -- full frame, relative to DATA_DIR
    belonging_id TEXT,                  -- folder id in belongings/ (NULL = generic)
    belonging_name TEXT,                -- e.g. "My Bag"
    event TEXT NOT NULL DEFAULT 'seen', -- 'seen' or 'left_view'
    confidence REAL,                    -- YOLO detection confidence
    match_score REAL,                   -- similarity to your reference photos
    bbox TEXT,                          -- "x1,y1,x2,y2" in pixels
    crop_path TEXT,                     -- close-up of the item
    nearby TEXT                         -- other objects in frame, comma separated
)
"""

NEW_COLUMNS = {
    "belonging_id": "TEXT",
    "belonging_name": "TEXT",
    "event": "TEXT NOT NULL DEFAULT 'seen'",
    "confidence": "REAL",
    "match_score": "REAL",
    "bbox": "TEXT",
    "crop_path": "TEXT",
    "nearby": "TEXT",
    # Filled in by Grok (interpreter.py)
    "verified": "INTEGER",        # 1 = Grok confirms it's yours, 0 = not yours, NULL = unchecked/unsure
    "verify_reason": "TEXT",
    "description": "TEXT",        # "On the gray folding table, left of the green bag"
    "place": "TEXT",              # short label: "hackathon table", "kitchen counter"
}

ALERTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    belonging_id TEXT NOT NULL,
    belonging_name TEXT NOT NULL,
    sighting_id INTEGER,          -- the sighting the alert is about
    reason TEXT NOT NULL,         -- rule that fired: 'out_of_sight' / 'moved_away'
    urgency TEXT NOT NULL DEFAULT 'low',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    dismissed INTEGER NOT NULL DEFAULT 0,
    UNIQUE (belonging_id, sighting_id)
)
"""

# People the user wants RecallAI to remember. known_since is a YYYY-MM-DD
# date; "how long you've known them" is computed from it when reading.
PEOPLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    relationship TEXT NOT NULL DEFAULT '',
    known_since TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def connect(path=None):
    path = path or DB_PATH
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=10)
    db.row_factory = sqlite3.Row
    return db


def _columns(db):
    return {row[1]: row for row in db.execute("PRAGMA table_info(sightings)")}


def init_db(path=None):
    """Create or upgrade the sightings table. Safe to call repeatedly."""
    with connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(ALERTS_SCHEMA)
        db.execute(PEOPLE_SCHEMA)
        cols = _columns(db)

        if not cols:
            db.execute(SCHEMA)
            cols = _columns(db)

        # Old schema had NOT NULL on latitude/longitude/accuracy.
        # SQLite can't drop a constraint, so rebuild the table once.
        if cols["latitude"][3]:  # notnull flag
            old_cols = ", ".join(cols)
            db.execute("ALTER TABLE sightings RENAME TO sightings_old")
            db.execute(SCHEMA)
            db.execute(
                f"INSERT INTO sightings ({old_cols}) "
                f"SELECT {old_cols} FROM sightings_old"
            )
            db.execute("DROP TABLE sightings_old")
            cols = _columns(db)

        for name, decl in NEW_COLUMNS.items():
            if name not in cols:
                db.execute(f"ALTER TABLE sightings ADD COLUMN {name} {decl}")


def insert_sighting(db, **fields):
    keys = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = db.execute(
        f"INSERT INTO sightings ({keys}) VALUES ({marks})",
        tuple(fields.values()),
    )
    db.commit()
    return cur.lastrowid
