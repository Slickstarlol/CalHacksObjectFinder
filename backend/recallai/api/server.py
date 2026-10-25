"""RecallAI web server: accounts, phone GPS relay, search page, Grok agent, alerts.

    python -m uvicorn recallai.api.server:app --host 0.0.0.0 --port 8000

Accounts (see docs/AUTH.md): every route that reads or changes user data
needs `Authorization: Bearer <token>` from /api/auth/login or /register and
only sees the caller's own belongings, sightings, alerts and people.
Device-only routes (the camera process) are guarded by auth.require_device.
"""

import base64
import re
import threading
import time
from contextlib import asynccontextmanager
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from fastapi import Depends, FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from recallai import owners, paths
from recallai.memory import db as dbmod
from recallai.ai.agent import RecallAgent, MemoryTools
from recallai.ai.describe import MAX_PHOTOS, cached as cached_description, describe, save_cache
from recallai.memory.people import find_or_create, get_person, names_by_id, set_description
from recallai.memory.people import router as people_router
from recallai.api import search
from recallai.api.auth import current_user, media_user, require_device
from recallai.api.auth import router as auth_router
from recallai.api.search import router as search_router
from recallai.vision.enroll import ANGLES
from recallai.sync.spacetime_bridge import SpacetimeBridge
from recallai.ai.watcher import Watcher
from recallai.vision.people_id import PERSON, SNAP_IOU, headshot_jpeg, person_folder, photos_of
from recallai.vision.recognition import box_iou, load_belongings, set_owner

SNAPSHOT_DIR = paths.SNAPSHOT_DIR   # data/snapshots, served (per owner) at /snapshots
WEB_DIST = paths.WEB_DIST           # frontend/live-dashboard/dist (npm run build)
(SNAPSHOT_DIR / "crops").mkdir(parents=True, exist_ok=True)
GPS_FRESH_SECONDS = 120
WATCH_INTERVAL = 60

dbmod.init_db()

# Phone GPS, per signed-in user: {user_id: {"latitude", "longitude",
# "accuracy", "timestamp"}}. In memory only, like before accounts.
_gps_lock = threading.Lock()
_gps = {}


def fresh_gps(owner_id=None):
    """That user's latest phone GPS if it's recent enough to trust, else None."""
    if owner_id is None:
        return None
    with _gps_lock:
        fix = _gps.get(int(owner_id))
    if not fix:
        return None
    age = (datetime.now(timezone.utc)
           - datetime.fromisoformat(fix["timestamp"])).total_seconds()
    return dict(fix) if age <= GPS_FRESH_SECONDS else None


def reset_gps():
    """Test helper."""
    with _gps_lock:
        _gps.clear()


# The Grok agent is shared; each request gets a copy of its tools bound to
# the caller's user id and GPS (MemoryTools.for_request).
agent = RecallAgent(tools=MemoryTools())


@asynccontextmanager
async def lifespan(app):
    Watcher(gps_provider=fresh_gps, interval=WATCH_INTERVAL).start()
    print("Khris (Grok agent):", "ON" if agent.enabled
          else "off (set XAI_API_KEY to enable)")
    print("Dashboard: cd frontend/live-dashboard && npm run dev  ->  http://localhost:5174"
          "  (built: http://localhost:8000/app/)")
    bridge = None
    if os.environ.get("SPACETIME_SYNC", "1").strip() != "0":
        bridge = SpacetimeBridge()
        bridge.start()
        print(f"Dashboard sync: mirroring to SpacetimeDB at {bridge.where}"
              " (SPACETIME_SYNC=0 to disable)")
    else:
        print("Dashboard sync: off (SPACETIME_SYNC=0)")
    yield
    if bridge:
        bridge.stop(timeout=2)


app = FastAPI(title="RecallAI", lifespan=lifespan)
# The Vite dev server proxies /api and /snapshots, but allow direct calls too:
# frontend/live-dashboard/ runs on :5174 (:5173 kept for other local dev servers).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_methods=["*"],
    # "*" already covers Authorization; listed for clarity.
    allow_headers=["Authorization", "Content-Type", "*"],
)
app.include_router(auth_router)
app.include_router(search_router)
app.include_router(people_router)


def _belongings_dir():
    return search.BELONGINGS_DIR   # read at call time (tests patch it)


def my_belonging_ids(user_id):
    return {b.id for b in owners.owned(load_belongings(_belongings_dir()), user_id)}


# ---------------------------------------------------------------------------
# Snapshots: only the owner of the sighting a file belongs to may load it.
# <img> tags can't send headers, so ?token= is accepted here (media_user).
# ---------------------------------------------------------------------------
@app.get("/snapshots/{path:path}")
def snapshot(path: str, user=Depends(media_user)):
    rel = PurePosixPath(path.replace("\\", "/"))
    if not rel.parts or rel.is_absolute() or ".." in rel.parts:
        raise HTTPException(404, "Not found")
    stored = "snapshots/" + rel.as_posix()
    variants = (stored, stored.replace("/", "\\"))
    with dbmod.connect() as db:
        row = db.execute(
            "SELECT 1 FROM sightings WHERE owner_id = ? AND (image_path IN (?, ?) "
            "OR crop_path IN (?, ?)) LIMIT 1",
            (user["id"], *variants, *variants)).fetchone()
    if row is None:
        raise HTTPException(404, "Not found")
    base = Path(SNAPSHOT_DIR).resolve()
    full = (base / Path(*rel.parts)).resolve()
    if base not in full.parents or not full.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(full, headers={"Cache-Control": "private, max-age=3600"})

# Latest annotated frame pushed by webcam.py. The React dashboard reads it.
LIVE_STALE_SECONDS = 2.5
_live_lock = threading.Lock()
_live = {
    "jpeg": None,
    "meta": {"gps": False, "width": None, "height": None, "in_view": [], "people": [],
             "detections": []},
    "updated_at": 0.0,
}


class LiveUpdate(BaseModel):
    frame_jpeg_b64: str = ""
    gps: bool = False
    width: int | None = None
    height: int | None = None
    in_view: list[dict] = Field(default_factory=list)
    people: list[dict] = Field(default_factory=list)
    detections: list[dict] = Field(default_factory=list)
    enroll: dict | None = None


def _live_view():
    with _live_lock:
        updated = _live["updated_at"]
        age = None if not updated else time.monotonic() - updated
        jpeg = _live["jpeg"]
        online = jpeg is not None and age is not None and age <= LIVE_STALE_SECONDS
        meta = {
            "gps": _live["meta"].get("gps", False),
            "width": _live["meta"].get("width"),
            "height": _live["meta"].get("height"),
            "in_view": list(_live["meta"].get("in_view") or []),
            "people": list(_live["meta"].get("people") or []),
            "detections": list(_live["meta"].get("detections") or []),
        }
    return {
        "online": online,
        "age_seconds": None if age is None else round(age, 2),
        **meta,
        "jpeg": jpeg,
    }


@app.post("/api/live")
def push_live(body: LiveUpdate, _device=Depends(require_device)):
    """webcam.py posts the annotated frame and the objects it is tracking.
    Device-only: from this machine, or with RECALLAI_DEVICE_KEY."""
    jpeg = b""
    if body.frame_jpeg_b64:
        try:
            jpeg = base64.b64decode(body.frame_jpeg_b64, validate=True)
        except Exception:
            raise HTTPException(400, "frame_jpeg_b64 is not valid base64")
        if len(jpeg) > 2_000_000:
            raise HTTPException(413, "frame too large")
        if not jpeg.startswith(b"\xff\xd8"):
            raise HTTPException(400, "frame is not a JPEG")
    meta = {
        "gps": body.gps,
        "width": body.width,
        "height": body.height,
        "in_view": body.in_view[:32],
        "people": body.people[:32],
        "detections": body.detections[:40],
    }
    with _live_lock:
        if jpeg:
            _live["jpeg"] = jpeg
        _live["meta"] = meta
        _live["updated_at"] = time.monotonic()
    if body.enroll:
        _apply_enroll(body.enroll)
    # The device sees owner_id: it writes it into the new belonging's info.json.
    return {"status": "ok", "enroll": enroll_view(), "people_jobs": take_people_jobs(),
            "belongings_version": search.belongings_version()}


# People named on the live feed. The dashboard sends the box a user
# double-clicked; the camera gets the job with its next POST /api/live,
# saves a photo of that person (data/people/<id>/) and names them from then on.
MAX_PEOPLE_JOBS = 8
_people_jobs_lock = threading.Lock()
_people_jobs = []


def take_people_jobs():
    with _people_jobs_lock:
        jobs = list(_people_jobs)
        _people_jobs.clear()
    return jobs


class LabelPerson(BaseModel):
    bbox: list[float] = Field(min_length=4, max_length=4)
    name: str | None = Field(default=None, max_length=80)
    person_id: int | None = None


# Camera enrollment. webcam.py does the seeing; this is the prompt the
# dashboard shows, plus the switch that tells the camera to collect angles.
# One camera = one session at a time, owned by the user who pressed Add.
_enroll_lock = threading.Lock()
_IDLE_ENROLL = {
    "active": False,
    "phase": "idle",
    "message": "",
    "category": None,
    "name": None,
    "saved": 0,
    "needed": ANGLES,
    "belonging_id": None,
    "owner_id": None,
}
_enroll = dict(_IDLE_ENROLL)
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def enroll_view():
    """Full state, including owner_id (for the device)."""
    with _enroll_lock:
        return dict(_enroll)


def enroll_view_for(user_id):
    """What one user's dashboard may see. Another account's session shows
    up only as "camera busy", without its item name or id."""
    view = enroll_view()
    owner = view.pop("owner_id")
    if owner is None or owner == user_id:
        view["mine"] = owner == user_id
        return view
    idle = {k: v for k, v in _IDLE_ENROLL.items() if k != "owner_id"}
    if view["active"]:
        return {**idle, "active": True, "phase": "busy", "mine": False,
                "message": "The camera is adding another account's item. "
                           "Try again in a moment."}
    return {**idle, "mine": False}


def _apply_enroll(report):
    """Progress from the camera. Starting and cancelling stay with the UI,
    so a late frame cannot turn the session back on."""
    if not isinstance(report, dict):
        return
    with _enroll_lock:
        if not _enroll["active"]:
            return
        for key in ("phase", "message", "category", "name", "saved", "belonging_id"):
            if key in report and report[key] is not None:
                _enroll[key] = report[key]
        saved = _enroll["saved"] or 0
        if report.get("phase") == "done" or saved >= _enroll["needed"]:
            _enroll["active"] = False
            _enroll["phase"] = "done"
        owner, bid = _enroll["owner_id"], _enroll["belonging_id"]
    # Belt and braces: an older camera process may not write owner_id itself.
    if owner is not None and isinstance(bid, str) and _SAFE_ID.fullmatch(bid):
        set_owner(Path(_belongings_dir()) / bid, owner)


def reset_enroll():
    """Test helper."""
    with _enroll_lock:
        _enroll.clear()
        _enroll.update(_IDLE_ENROLL)


@app.get("/api/enroll")
def enroll_status(user=Depends(current_user)):
    return enroll_view_for(user["id"])


@app.post("/api/enroll/start")
def enroll_start(user=Depends(current_user)):
    with _enroll_lock:
        if _enroll["active"] and _enroll["owner_id"] != user["id"]:
            raise HTTPException(409, "The camera is adding another account's item. "
                                     "Try again in a moment.")
        if not _enroll["active"]:
            _enroll.clear()
            _enroll.update({**_IDLE_ENROLL, "active": True, "phase": "hold",
                            "message": "Show one object and hold it still.",
                            "owner_id": user["id"]})
    return enroll_view_for(user["id"])


@app.post("/api/enroll/cancel")
def enroll_cancel(user=Depends(current_user)):
    """Stop collecting. Angles already saved stay on disk."""
    with _enroll_lock:
        if _enroll["owner_id"] not in (None, user["id"]) and _enroll["active"]:
            raise HTTPException(409, "Only the account that started adding "
                                     "this item can cancel it.")
        if _enroll["owner_id"] in (None, user["id"]):
            _enroll["active"] = False
            if _enroll["phase"] != "done":
                _enroll["phase"] = "idle"
                _enroll["message"] = ""
    return enroll_view_for(user["id"])


@app.get("/api/live")
def live_status(user=Depends(current_user)):
    """Camera status. `in_view` (recognized belongings) lists only the
    caller's items; `detections` are generic YOLO categories (no owner)."""
    snap = _live_view()
    snap.pop("jpeg", None)
    mine = my_belonging_ids(user["id"])
    snap["in_view"] = [i for i in snap["in_view"]
                       if isinstance(i, dict) and i.get("belonging_id") in mine]
    # Only the caller's people, with today's name (a rename shows at once).
    names = names_by_id(user["id"])
    snap["people"] = [{**p, "name": names[p["person_id"]]} for p in snap["people"]
                      if isinstance(p, dict) and p.get("person_id") in names]
    return snap


@app.post("/api/live/people")
def label_live_person(body: LabelPerson, user=Depends(current_user)):
    """Name the person in a box on the live feed (double-click in the
    dashboard). `person_id` picks one of your people; otherwise `name` finds
    your person with that name or creates them. The box is matched to a
    person the camera sees right now, and the camera learns their look."""
    snap = _live_view()
    if not snap["online"]:
        raise HTTPException(409, "The camera is off.")
    boxes = [d["bbox"] for d in snap["detections"]
             if isinstance(d, dict) and d.get("category") == PERSON
             and isinstance(d.get("bbox"), list) and len(d["bbox"]) == 4]
    best = max(boxes, key=lambda b: box_iou(body.bbox, b), default=None)
    if best is None or box_iou(body.bbox, best) < SNAP_IOU:
        raise HTTPException(409, "That person isn't in view any more.")
    if body.person_id is not None:
        person, created = get_person(body.person_id, user["id"]), False
        if person is None:
            raise HTTPException(404, "No such person")
    else:
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(422, "Name is required")
        person, created = find_or_create(name, user["id"])
    with _people_jobs_lock:
        _people_jobs.append({"person_id": person["id"], "bbox": best,
                             "width": snap["width"]})
        del _people_jobs[:-MAX_PEOPLE_JOBS]
    return {"person": person, "created": created, "bbox": best}


class DescribeBody(BaseModel):
    refresh: bool = False


@app.post("/api/people/{person_id}/describe")
def describe_person(person_id: int, body: DescribeBody | None = None,
                    user=Depends(current_user)):
    """Grok describes one of your people from the camera's photos of them
    (click a person in the People panel). The answer is kept until new
    photos arrive; `refresh` asks again. It becomes their notes if they had
    none yet."""
    person = get_person(person_id, user["id"])
    if person is None:
        raise HTTPException(404, "No such person")
    photos = photos_of(person_id)[:MAX_PHOTOS]
    if not photos:
        raise HTTPException(409, f"No photos of {person['name']} yet. Name them on "
                                 "the live feed and the camera saves some.")
    folder = person_folder(person_id)
    result = None if body and body.refresh else cached_description(folder, photos)
    fresh = result is None
    if fresh:
        if agent.client is None:
            raise HTTPException(503, "Khris isn't set up: add XAI_API_KEY to .env "
                                     "and restart the server.")
        try:
            found = describe(agent.client, person, photos,
                             owner=user.get("display_name") or "the user")
        except Exception as e:
            raise HTTPException(502, f"Khris couldn't describe {person['name']} ({e}).")
        result = save_cache(folder, photos, found)
    saved = bool(fresh and result["description"] and not person["description"]
                 and set_description(person_id, user["id"], result["description"]))
    return {
        "summary": result["summary"],
        "description": result["description"],
        "same_person": result["same_person"],
        "photos": [f"/api/people/{person_id}/photos/{p.name}" for p in photos],
        "cached": not fresh,
        "saved_to_notes": saved,
        "at": result.get("at"),
    }


@app.get("/api/people/{person_id}/photos/{name}")
def person_photo(person_id: int, name: str, user=Depends(media_user)):
    """One of the camera's photos of your person (for <img>, so ?token= works)."""
    if get_person(person_id, user["id"]) is None:
        raise HTTPException(404, "Not found")
    photo = next((p for p in photos_of(person_id) if p.name == name), None)
    if photo is None:
        raise HTTPException(404, "Not found")
    return FileResponse(photo, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/device/gps")
def device_gps(_device=Depends(require_device)):
    """Device-only: every user's fresh phone GPS, so the camera can attach
    each belonging's OWNER's location to its sighting."""
    with _gps_lock:
        ids = list(_gps)
    by_owner = {str(uid): fix for uid in ids if (fix := fresh_gps(uid))}
    return {"by_owner": by_owner}


@app.get("/api/people/{person_id}/avatar")
def person_avatar(person_id: int, user=Depends(media_user)):
    """Headshot from the first photo the camera saved of your person (the
    profile picture): the top of their whole-body crop, squared."""
    if get_person(person_id, user["id"]) is None:
        raise HTTPException(404, "Not found")
    photos = photos_of(person_id)          # newest first -> the last is the first taken
    jpeg = headshot_jpeg(photos[-1]) if photos else None
    if jpeg is None:
        raise HTTPException(404, "No photo yet")
    return Response(jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=60"})


@app.get("/api/live/frame.jpg")
def live_frame(_user=Depends(media_user)):
    snap = _live_view()
    if not snap["online"] or not snap["jpeg"]:
        raise HTTPException(404, "Camera offline")
    return Response(
        content=snap["jpeg"],
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/live/stream")
def live_stream(_user=Depends(media_user)):
    """MJPEG mirror of the Python camera, for an <img> tag."""
    def frames():
        while True:
            snap = _live_view()
            if snap["online"] and snap["jpeg"]:
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                    + snap["jpeg"] + b"\r\n"
                )
            time.sleep(0.12)

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store"},
    )


class GPSData(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy: float = Field(ge=0)


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[Turn] = []
    timezone: str | None = Field(None, max_length=64)  # browser IANA zone, optional


@app.get("/", response_class=HTMLResponse)
def home():
    return (paths.STATIC_DIR / "gps.html").read_text(encoding="utf-8")


@app.get("/auth.js")
def auth_script():
    """Sign-in helper shared by the plain HTML pages (gps.html, search.html)."""
    return Response((paths.STATIC_DIR / "auth.js").read_text(encoding="utf-8"),
                    media_type="text/javascript",
                    headers={"Cache-Control": "no-cache"})


@app.post("/gps")
@app.post("/api/gps")   # same, under /api so the dashboard's dev proxy forwards it
def update_gps(data: GPSData, user=Depends(current_user)):
    """The signed-in user's phone location (it is theirs, not shared)."""
    with _gps_lock:
        _gps[user["id"]] = {
            "latitude": data.latitude,
            "longitude": data.longitude,
            "accuracy": data.accuracy,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    return {"status": "GPS received"}


@app.get("/gps/latest")
def get_latest_gps(user=Depends(current_user)):
    with _gps_lock:
        fix = _gps.get(user["id"])
    return dict(fix) if fix else {"status": "No GPS data"}


# ---------------------------------------------------------------------------
# Grok agent
# ---------------------------------------------------------------------------
@app.post("/api/ask")
async def ask(q: Question, user=Depends(current_user)):
    """Natural-language question about your belongings -> Grok's answer.
    Grok's tools only see the caller's data (owner fixed here, not by the model)."""
    uid = user["id"]
    try:
        return await run_in_threadpool(
            agent.ask, q.question, [t.model_dump() for t in q.history], q.timezone,
            owner_id=uid, gps_provider=lambda: fresh_gps(uid))
    except Exception as e:
        raise HTTPException(502, f"Grok request failed: {e}")


# ---------------------------------------------------------------------------
# Proactive alerts
# ---------------------------------------------------------------------------
@app.get("/api/alerts")
def list_alerts(include_dismissed: bool = False, user=Depends(current_user)):
    """Your alerts only."""
    with dbmod.connect() as db:
        rows = db.execute(
            "SELECT a.*, s.crop_path, s.description, s.timestamp AS seen_at "
            "FROM alerts a LEFT JOIN sightings s ON s.id = a.sighting_id "
            "AND s.owner_id = a.owner_id WHERE a.owner_id = ? "
            + ("" if include_dismissed else "AND a.dismissed = 0 ")
            + "ORDER BY a.created_at DESC LIMIT 20", (user["id"],)).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/alerts/{alert_id}/dismiss")
def dismiss_alert(alert_id: int, user=Depends(current_user)):
    """Dismiss one of your alerts (someone else's id -> 404, like a missing one)."""
    with dbmod.connect() as db:
        cur = db.execute("UPDATE alerts SET dismissed=1 WHERE id=? AND owner_id=?",
                         (alert_id, user["id"]))
        db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "No such alert")
    return {"status": "dismissed"}


# ---------------------------------------------------------------------------
# Built React dashboard at /app (mounted last so it never shadows a route)
# ---------------------------------------------------------------------------
if (WEB_DIST / "index.html").is_file():
    app.mount("/app", StaticFiles(directory=str(WEB_DIST), html=True), name="app")
