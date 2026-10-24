"""RecallAI web server: phone GPS relay, search page, Grok agent, alerts.

    uvicorn server:app --host 0.0.0.0 --port 8000
"""

import base64
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db as dbmod
from agent import RecallAgent, MemoryTools
from people import router as people_router
from search import router as search_router
from watcher import Watcher

BASE_DIR = Path(__file__).resolve().parent
SNAPSHOT_DIR = BASE_DIR / "snapshots"
(SNAPSHOT_DIR / "crops").mkdir(parents=True, exist_ok=True)
GPS_FRESH_SECONDS = 120
WATCH_INTERVAL = 60

dbmod.init_db()

latest_gps = None


def fresh_gps():
    """Latest phone GPS if it's recent enough to trust, else None."""
    if not latest_gps:
        return None
    age = (datetime.now(timezone.utc)
           - datetime.fromisoformat(latest_gps["timestamp"])).total_seconds()
    return latest_gps if age <= GPS_FRESH_SECONDS else None


agent = RecallAgent(tools=MemoryTools(gps_provider=fresh_gps))


@asynccontextmanager
async def lifespan(app):
    Watcher(gps_provider=fresh_gps, interval=WATCH_INTERVAL).start()
    print("Grok agent:", "ON" if agent.enabled
          else "off (set XAI_API_KEY to enable)")
    print("Dashboard: cd RecallAIFrontEnd && npm run dev  ->  http://localhost:5173")
    yield


app = FastAPI(title="RecallAI", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(search_router)
app.include_router(people_router)
app.mount("/snapshots", StaticFiles(directory=str(SNAPSHOT_DIR)), name="snapshots")

# Latest annotated frame pushed by webcam.py. The React dashboard reads it.
LIVE_STALE_SECONDS = 2.5
_live_lock = threading.Lock()
_live = {
    "jpeg": None,
    "meta": {"gps": False, "width": None, "height": None, "in_view": [], "detections": []},
    "updated_at": 0.0,
}


class LiveUpdate(BaseModel):
    frame_jpeg_b64: str = ""
    gps: bool = False
    width: int | None = None
    height: int | None = None
    in_view: list[dict] = Field(default_factory=list)
    detections: list[dict] = Field(default_factory=list)


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
            "detections": list(_live["meta"].get("detections") or []),
        }
    return {
        "online": online,
        "age_seconds": None if age is None else round(age, 2),
        **meta,
        "jpeg": jpeg,
    }


@app.post("/api/live")
def push_live(body: LiveUpdate):
    """webcam.py posts the annotated frame and the objects it is tracking."""
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
        "detections": body.detections[:40],
    }
    with _live_lock:
        if jpeg:
            _live["jpeg"] = jpeg
        _live["meta"] = meta
        _live["updated_at"] = time.monotonic()
    return {"status": "ok"}


@app.get("/api/live")
def live_status():
    snap = _live_view()
    snap.pop("jpeg", None)
    return snap


@app.get("/api/live/frame.jpg")
def live_frame():
    snap = _live_view()
    if not snap["online"] or not snap["jpeg"]:
        raise HTTPException(404, "Camera offline")
    return Response(
        content=snap["jpeg"],
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/live/stream")
def live_stream():
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
    return (BASE_DIR / "gps.html").read_text(encoding="utf-8")


@app.post("/gps")
def update_gps(data: GPSData):
    global latest_gps
    latest_gps = {
        "latitude": data.latitude,
        "longitude": data.longitude,
        "accuracy": data.accuracy,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return {"status": "GPS received"}


@app.get("/gps/latest")
def get_latest_gps():
    return latest_gps or {"status": "No GPS data"}


# ---------------------------------------------------------------------------
# Grok agent
# ---------------------------------------------------------------------------
@app.post("/api/ask")
async def ask(q: Question):
    """Natural-language question about your belongings -> Grok's answer."""
    try:
        return await run_in_threadpool(
            agent.ask, q.question, [t.model_dump() for t in q.history], q.timezone)
    except Exception as e:
        raise HTTPException(502, f"Grok request failed: {e}")


# ---------------------------------------------------------------------------
# Proactive alerts
# ---------------------------------------------------------------------------
@app.get("/api/alerts")
def list_alerts(include_dismissed: bool = False):
    with dbmod.connect() as db:
        rows = db.execute(
            "SELECT a.*, s.crop_path, s.description, s.timestamp AS seen_at "
            "FROM alerts a LEFT JOIN sightings s ON s.id = a.sighting_id "
            + ("" if include_dismissed else "WHERE a.dismissed = 0 ")
            + "ORDER BY a.created_at DESC LIMIT 20").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/alerts/{alert_id}/dismiss")
def dismiss_alert(alert_id: int):
    with dbmod.connect() as db:
        cur = db.execute("UPDATE alerts SET dismissed=1 WHERE id=?", (alert_id,))
        db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "No such alert")
    return {"status": "dismissed"}
