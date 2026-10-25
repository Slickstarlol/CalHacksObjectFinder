"""People the user wants RecallAI to remember.

Each person has a description, their relationship to the user, and the date
they met (`known_since`). How long they have known each other is derived
from that date so it stays current.

    GET    /api/people
    POST   /api/people
    PUT    /api/people/{id}
    DELETE /api/people/{id}

People are private per account: every route needs a signed-in user and
only sees and changes rows with that user's owner_id (others' ids -> 404).
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from recallai import owners
from recallai.api.auth import current_user
from recallai.memory import db as dbmod

router = APIRouter()


class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    relationship: str = Field(default="", max_length=80)
    known_since: str | None = None


def _n(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def known_for(known_since, today=None):
    """'2 years and 3 months' from a YYYY-MM-DD date, or None."""
    if not known_since:
        return None
    try:
        start = date.fromisoformat(str(known_since)[:10])
    except ValueError:
        return None
    today = today or date.today()
    if start > today:
        return None
    years = today.year - start.year
    months = today.month - start.month
    if today.day < start.day:
        months -= 1
    if months < 0:
        years -= 1
        months += 12
    parts = []
    if years:
        parts.append(_n(years, "year"))
    if months:
        parts.append(_n(months, "month"))
    if parts:
        return " and ".join(parts)
    days = (today - start).days
    if days == 0:
        return "since today"
    return _n(days, "day")


def _clean(body: PersonIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Name is required")
    known = (body.known_since or "").strip() or None
    if known:
        try:
            started = date.fromisoformat(known)
        except ValueError:
            raise HTTPException(422, "known_since must be YYYY-MM-DD")
        if started > date.today():
            raise HTTPException(422, "known_since cannot be in the future")
        known = started.isoformat()
    return {
        "name": name,
        "description": body.description.strip(),
        "relationship": body.relationship.strip(),
        "known_since": known,
    }


def _row(row, today=None):
    data = dict(row)
    data["known_for"] = known_for(data.get("known_since"), today)
    return data


def list_people(db_path=None, today=None, owner_id=owners.ANY):
    dbmod.init_db(db_path)
    own, params = owners.clause(owner_id)
    with dbmod.connect(db_path) as db:
        rows = db.execute(
            f"SELECT * FROM people WHERE {own} ORDER BY name COLLATE NOCASE, id", params
        ).fetchall()
    return [_row(r, today) for r in rows]


def _now():
    return datetime.now(timezone.utc).isoformat()


@router.get("/api/people")
def get_people(user=Depends(current_user)):
    return list_people(owner_id=user["id"])


@router.post("/api/people", status_code=201)
def create_person(body: PersonIn, user=Depends(current_user)):
    fields = _clean(body)
    now = _now()
    dbmod.init_db()
    with dbmod.connect() as db:
        cur = db.execute(
            "INSERT INTO people (name, description, relationship, known_since, "
            "created_at, updated_at, owner_id) VALUES (?,?,?,?,?,?,?)",
            (fields["name"], fields["description"], fields["relationship"],
             fields["known_since"], now, now, user["id"]),
        )
        db.commit()
        row = db.execute("SELECT * FROM people WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row(row)


@router.put("/api/people/{person_id}")
def update_person(person_id: int, body: PersonIn, user=Depends(current_user)):
    fields = _clean(body)
    now = _now()
    dbmod.init_db()
    with dbmod.connect() as db:
        cur = db.execute(
            "UPDATE people SET name=?, description=?, relationship=?, "
            "known_since=?, updated_at=? WHERE id=? AND owner_id=?",
            (fields["name"], fields["description"], fields["relationship"],
             fields["known_since"], now, person_id, user["id"]),
        )
        db.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "No such person")
        row = db.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
    return _row(row)


@router.delete("/api/people/{person_id}")
def delete_person(person_id: int, user=Depends(current_user)):
    dbmod.init_db()
    with dbmod.connect() as db:
        cur = db.execute("DELETE FROM people WHERE id=? AND owner_id=?",
                         (person_id, user["id"]))
        db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "No such person")
    return {"status": "deleted"}
