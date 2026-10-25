"""Search memory: where did I last see my belonging?"""


from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from recallai import paths
from recallai.memory import db as dbmod
from recallai.memory import temporal_memory as tm
from recallai.vision.recognition import BELONGINGS_DIR, load_belongings

router = APIRouter()

COLUMNS = """id, object_name, latitude, longitude, accuracy, timestamp,
             image_path, belonging_id, belonging_name, event, confidence,
             match_score, bbox, crop_path, nearby, verified, verify_reason,
             description, place"""

# Sightings Grok rejected ("not your item") are kept for debugging but hidden.
VISIBLE = "COALESCE(verified, 1) != 0"


def row_to_sighting(row):
    s = dict(row)
    lat, lon = s["latitude"], s["longitude"]
    s["maps_url"] = (
        f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=19/{lat}/{lon}"
        if lat is not None and lon is not None else None
    )
    s["nearby"] = [n for n in (s["nearby"] or "").split(", ") if n]
    s["label"] = s["belonging_name"] or s["object_name"]
    return s


@router.get("/search", response_class=HTMLResponse)
def search_page():
    return (paths.STATIC_DIR / "search.html").read_text(encoding="utf-8")


@router.get("/api/belongings")
def list_belongings():
    """Every registered belonging, with its most recent sighting (if any)."""
    out = []
    now = tm.utcnow()
    with dbmod.connect() as db:
        for b in load_belongings(BELONGINGS_DIR):
            if not b.tracking:
                continue
            row = db.execute(
                f"SELECT {COLUMNS} FROM sightings WHERE belonging_id = ? "
                f"AND {VISIBLE} ORDER BY timestamp DESC, id DESC LIMIT 1", (b.id,)
            ).fetchone()
            n = db.execute(
                f"SELECT COUNT(*) FROM sightings WHERE belonging_id = ? AND {VISIBLE}",
                (b.id,)
            ).fetchone()[0]
            out.append({
                "id": b.id, "name": b.name, "category": b.category,
                "photos": len(b.reference_paths), "sightings": n,
                "last": row_to_sighting(row) if row else None,
                # Additive: computed age, verification and recording-gap state.
                "last_seen": tm.get_last_seen(b.id, now=now, name=b.name),
            })
    out.sort(key=lambda x: (x["last"] or {}).get("timestamp") or "", reverse=True)
    return out


@router.get("/api/belongings/{belonging_id}/last-seen")
def belonging_last_seen(
    belonging_id: str,
    at: str | None = Query(None, description="ISO 8601: last sighting at or before this time"),
    history: int = Query(5, ge=0, le=50),
):
    """When/where this belonging was last seen (optionally as of `at`)."""
    names = {b.id: b.name for b in load_belongings(BELONGINGS_DIR)}
    if belonging_id not in names:
        raise HTTPException(404, f'No registered belonging "{belonging_id}"')
    try:
        return tm.get_last_seen(belonging_id, belongings_dir=BELONGINGS_DIR, at=at,
                                history=history, name=names[belonging_id])
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/api/objects")
def list_objects():
    """Generic object categories seen (includes pre-recognition sightings)."""
    with dbmod.connect() as db:
        rows = db.execute(
            "SELECT object_name, COUNT(*), MAX(timestamp) FROM sightings "
            "GROUP BY object_name ORDER BY MAX(timestamp) DESC"
        ).fetchall()
    return [{"object_name": n, "count": c, "last_seen": t} for n, c, t in rows]


def _query(q, limit, belonging_id=None):
    with dbmod.connect() as db:
        if belonging_id:
            return db.execute(
                f"SELECT {COLUMNS} FROM sightings WHERE belonging_id = ? "
                f"AND {VISIBLE} ORDER BY timestamp DESC, id DESC LIMIT ?",
                (belonging_id, limit)
            ).fetchall()
        pattern = f"%{q.strip().lower()}%"
        # Your named belongings first, then generic category sightings.
        return db.execute(
            f"""SELECT {COLUMNS} FROM sightings
                WHERE (LOWER(COALESCE(belonging_name, '')) LIKE ?
                       OR LOWER(object_name) LIKE ?
                       OR LOWER(COALESCE(place, '')) LIKE ?)
                  AND {VISIBLE}
                ORDER BY (belonging_id IS NULL), timestamp DESC, id DESC
                LIMIT ?""",
            (pattern, pattern, pattern, limit),
        ).fetchall()


@router.get("/api/sightings")
def search_sightings(
    q: str = Query("", description="Belonging name or object category"),
    belonging_id: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    if not q.strip() and not belonging_id:
        raise HTTPException(422, "Provide q or belonging_id")
    return [row_to_sighting(r) for r in _query(q, limit, belonging_id)]


@router.get("/api/sightings/recent")
def recent_sightings(limit: int = Query(30, ge=1, le=100)):
    """Newest sightings of registered belongings, for the tracking timeline."""
    with dbmod.connect() as db:
        rows = db.execute(
            f"SELECT {COLUMNS} FROM sightings WHERE belonging_id IS NOT NULL "
            f"AND {VISIBLE} ORDER BY timestamp DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [row_to_sighting(r) for r in rows]


@router.get("/api/sightings/latest")
def latest_sighting(
    q: str = Query("", description="Belonging name or object category"),
    belonging_id: str | None = None,
):
    if not q.strip() and not belonging_id:
        raise HTTPException(422, "Provide q or belonging_id")
    rows = _query(q, 1, belonging_id)
    if not rows:
        raise HTTPException(404, f'No sightings found for "{q or belonging_id}"')
    return row_to_sighting(rows[0])
