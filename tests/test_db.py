import shutil
import sqlite3
from pathlib import Path

import db as dbmod

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
