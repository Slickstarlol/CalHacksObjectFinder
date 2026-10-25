"""Search memory: where did I last see my belonging?

Every /api route here requires a signed-in user and only reads that user's
belongings and sightings (owner_id). The /search page itself is public: it
is just the UI, and it signs in before calling the API.
"""


from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from recallai import owners, paths
from recallai.api.auth import current_user
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


def my_belongings(owner_id):
    """The caller's belongings on disk (BELONGINGS_DIR read at call time)."""
    return owners.owned(load_belongings(BELONGINGS_DIR), owner_id)


@router.get("/api/belongings")
def list_belongings(user=Depends(current_user)):
    """Your registered belongings, with their most recent sighting (if any)."""
    uid = user["id"]
    out = []
    now = tm.utcnow()
    with dbmod.connect() as db:
        for b in my_belongings(uid):
            if not b.tracking:
                continue
            row = db.execute(
                f"SELECT {COLUMNS} FROM sightings WHERE belonging_id = ? AND owner_id = ? "
                f"AND {VISIBLE} ORDER BY timestamp DESC, id DESC LIMIT 1", (b.id, uid)
            ).fetchone()
            n = db.execute(
                f"SELECT COUNT(*) FROM sightings WHERE belonging_id = ? AND owner_id = ? "
                f"AND {VISIBLE}", (b.id, uid)
            ).fetchone()[0]
            out.append({
                "id": b.id, "name": b.name, "category": b.category,
                "photos": len(b.reference_paths), "sightings": n,
                "last": row_to_sighting(row) if row else None,
                # Additive: computed age, verification and recording-gap state.
                "last_seen": tm.get_last_seen(b.id, now=now, name=b.name, owner_id=uid),
            })
    out.sort(key=lambda x: (x["last"] or {}).get("timestamp") or "", reverse=True)
    return out


@router.get("/api/belongings/{belonging_id}/last-seen")
def belonging_last_seen(
    belonging_id: str,
    at: str | None = Query(None, description="ISO 8601: last sighting at or before this time"),
    history: int = Query(5, ge=0, le=50),
    user=Depends(current_user),
):
    """When/where this belonging was last seen (optionally as of `at`)."""
    names = {b.id: b.name for b in my_belongings(user["id"])}
    if belonging_id not in names:
        raise HTTPException(404, f'No registered belonging "{belonging_id}"')
    try:
        return tm.get_last_seen(belonging_id, belongings_dir=BELONGINGS_DIR, at=at,
                                history=history, name=names[belonging_id],
                                owner_id=user["id"])
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/api/objects")
def list_objects(user=Depends(current_user)):
    """Generic object categories seen (includes pre-recognition sightings)."""
    with dbmod.connect() as db:
        rows = db.execute(
            "SELECT object_name, COUNT(*), MAX(timestamp) FROM sightings "
            "WHERE owner_id = ? GROUP BY object_name ORDER BY MAX(timestamp) DESC",
            (user["id"],)).fetchall()
    return [{"object_name": n, "count": c, "last_seen": t} for n, c, t in rows]


def _query(q, limit, owner_id, belonging_id=None):
    with dbmod.connect() as db:
        if belonging_id:
            return db.execute(
                f"SELECT {COLUMNS} FROM sightings WHERE belonging_id = ? AND owner_id = ? "
                f"AND {VISIBLE} ORDER BY timestamp DESC, id DESC LIMIT ?",
                (belonging_id, owner_id, limit)
            ).fetchall()
        pattern = f"%{q.strip().lower()}%"
        # Your named belongings first, then generic category sightings.
        return db.execute(
            f"""SELECT {COLUMNS} FROM sightings
                WHERE (LOWER(COALESCE(belonging_name, '')) LIKE ?
                       OR LOWER(object_name) LIKE ?
                       OR LOWER(COALESCE(place, '')) LIKE ?)
                  AND owner_id = ? AND {VISIBLE}
                ORDER BY (belonging_id IS NULL), timestamp DESC, id DESC
                LIMIT ?""",
            (pattern, pattern, pattern, owner_id, limit),
        ).fetchall()


@router.get("/api/sightings")
def search_sightings(
    q: str = Query("", description="Belonging name or object category"),
    belonging_id: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    user=Depends(current_user),
):
    if not q.strip() and not belonging_id:
        raise HTTPException(422, "Provide q or belonging_id")
    return [row_to_sighting(r) for r in _query(q, limit, user["id"], belonging_id)]


@router.get("/api/sightings/recent")
def recent_sightings(limit: int = Query(30, ge=1, le=100), user=Depends(current_user)):
    """Your newest sightings of registered belongings, for the tracking timeline."""
    with dbmod.connect() as db:
        rows = db.execute(
            f"SELECT {COLUMNS} FROM sightings WHERE belonging_id IS NOT NULL "
            f"AND owner_id = ? AND {VISIBLE} ORDER BY timestamp DESC, id DESC LIMIT ?",
            (user["id"], limit),
        ).fetchall()
    return [row_to_sighting(r) for r in rows]


@router.get("/api/sightings/latest")
def latest_sighting(
    q: str = Query("", description="Belonging name or object category"),
    belonging_id: str | None = None,
    user=Depends(current_user),
):
    if not q.strip() and not belonging_id:
        raise HTTPException(422, "Provide q or belonging_id")
    rows = _query(q, 1, user["id"], belonging_id)
    if not rows:
        raise HTTPException(404, f'No sightings found for "{q or belonging_id}"')
    return row_to_sighting(rows[0])
