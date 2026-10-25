import shutil
import sqlite3
from pathlib import Path

from recallai.memory import db as dbmod

LEGACY_SCHEMA = """
CREATE TABLE sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_name TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    accuracy REAL NOT NULL,
    timestamp TEXT NOT NULL,
    image_path TEXT NOT NULL
)"""


def cols(path):
    c = sqlite3.connect(path)
    out = {r[1]: r[3] for r in c.execute("PRAGMA table_info(sightings)")}
    c.close()
    return out


def test_fresh_db_has_full_schema(tmp_path):
    p = tmp_path / "m.db"
    dbmod.init_db(p)
    c = cols(p)
    assert set(dbmod.NEW_COLUMNS) <= set(c)
    assert c["latitude"] == 0  # nullable: GPS is optional


def test_legacy_db_is_migrated_without_losing_rows(tmp_path):
    p = tmp_path / "m.db"
    c = sqlite3.connect(p)
    c.execute(LEGACY_SCHEMA)
    c.execute("INSERT INTO sightings (object_name, latitude, longitude, accuracy,"
              " timestamp, image_path) VALUES ('laptop', 37.8, -122.4, 15,"
              " '2026-10-24T14:14:14+00:00', 'snapshots/a.jpg')")
    c.commit(); c.close()

    dbmod.init_db(p)
    dbmod.init_db(p)  # idempotent

    c = cols(p)
    assert c["latitude"] == 0
    assert "belonging_id" in c and "crop_path" in c
    with dbmod.connect(p) as db:
        rows = db.execute("SELECT * FROM sightings").fetchall()
        assert len(rows) == 1
        assert rows[0]["object_name"] == "laptop"
        assert rows[0]["event"] == "seen"
        # A sighting without GPS can now be stored
        dbmod.insert_sighting(db, object_name="mouse", timestamp="t",
                              image_path="x.jpg")
        assert db.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] == 2


def test_users_real_memory_db_migrates(tmp_path):
    real = Path(dbmod.__file__).parent / "memory.db"
    if not real.exists():
        return
    p = tmp_path / "copy.db"
    shutil.copy(real, p)
    with sqlite3.connect(p) as c:
        before = c.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
    dbmod.init_db(p)
    with dbmod.connect(p) as db:
        after = db.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
    assert after == before


# Exact schema written by the previous release (a26142a: sightings + alerts,
# Grok columns, no people table) -- what a teammate's existing memory.db has.
PREVIOUS_RELEASE_SCHEMA = """
CREATE TABLE sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_name TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    accuracy REAL,
    timestamp TEXT NOT NULL,
    image_path TEXT NOT NULL,
    belonging_id TEXT,
    belonging_name TEXT,
    event TEXT NOT NULL DEFAULT 'seen',
    confidence REAL,
    match_score REAL,
    bbox TEXT,
    crop_path TEXT,
    nearby TEXT,
    verified INTEGER,
    verify_reason TEXT,
    description TEXT,
    place TEXT
);
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    belonging_id TEXT NOT NULL,
    belonging_name TEXT NOT NULL,
    sighting_id INTEGER,
    reason TEXT NOT NULL,
    urgency TEXT NOT NULL DEFAULT 'low',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    dismissed INTEGER NOT NULL DEFAULT 0,
    UNIQUE (belonging_id, sighting_id)
);
"""


def _dump(path):
    with dbmod.connect(path) as db:
        return {t: [dict(r) for r in db.execute(f"SELECT * FROM {t} ORDER BY id")]
                for t in ("sightings", "alerts")}


def test_previous_release_db_gains_people_without_data_loss(tmp_path):
    p = tmp_path / "memory.db"
    c = sqlite3.connect(p)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(PREVIOUS_RELEASE_SCHEMA)
    c.execute(
        "INSERT INTO sightings (object_name, latitude, longitude, accuracy,"
        " timestamp, image_path, belonging_id, belonging_name, event, confidence,"
        " match_score, bbox, crop_path, nearby, verified, verify_reason,"
        " description, place) VALUES ('backpack', 37.87, -122.26, 12.5,"
        " '2026-10-24T14:00:00+00:00', 'snapshots/1.jpg', 'abc', 'My Bag',"
        " 'left_view', 0.91, 0.83, '1,2,3,4', 'snapshots/crops/1.jpg',"
        " 'laptop, bottle', 1, 'same zipper', 'On the gray table', 'hackathon table')")
    c.execute(
        "INSERT INTO sightings (object_name, timestamp, image_path)"
        " VALUES ('mouse', '2026-10-24T14:05:00+00:00', 'snapshots/2.jpg')")
    c.execute(
        "INSERT INTO alerts (belonging_id, belonging_name, sighting_id, reason,"
        " urgency, message, created_at, dismissed) VALUES ('abc', 'My Bag', 1,"
        " 'out_of_sight', 'high', 'Did you forget your bag?',"
        " '2026-10-24T14:20:00+00:00', 1)")
    c.commit()
    c.close()
    before = _dump(p)

    dbmod.init_db(p)
    dbmod.init_db(p)  # idempotent

    after = _dump(p)
    # every value survives; the only change is the new owner_id column, NULL
    # until the first account claims the rows
    assert {t: [{k: r[k] for k in before[t][0]} for r in after[t]] for t in after} == before
    assert all(r["owner_id"] is None for t in after for r in after[t])
    with dbmod.connect(p) as db:
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"sightings", "alerts", "people"} <= tables
        people_cols = {r[1] for r in db.execute("PRAGMA table_info(people)")}
        assert people_cols == {"id", "name", "description", "relationship",
                               "known_since", "created_at", "updated_at", "owner_id"}
        assert db.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 0
        # New rows still work in every table, ids continue after the old ones.
        new_id = dbmod.insert_sighting(db, object_name="cup", timestamp="t",
                                       image_path="x.jpg")
        assert new_id == 3
        db.execute("INSERT INTO people (name, created_at, updated_at)"
                   " VALUES ('Chawin', 'now', 'now')")
        db.commit()

    from recallai.memory import people
    assert [r["name"] for r in people.list_people(p)] == ["Chawin"]
