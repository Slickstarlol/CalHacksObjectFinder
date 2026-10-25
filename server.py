"""RecallAI web server: phone GPS relay, search page, Grok agent, alerts.

    uvicorn server:app --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db as dbmod
from agent import RecallAgent, MemoryTools
from search import router as search_router
from spacetime_bridge import SpacetimeBridge
from watcher import Watcher

BASE_DIR = Path(__file__).resolve().parent
SNAPSHOT_DIR = BASE_DIR / "snapshots"
WEB_DIST = BASE_DIR / "web" / "dist"   # built React dashboard (npm run build)
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
app.include_router(search_router)
app.mount("/snapshots", StaticFiles(directory=str(SNAPSHOT_DIR)), name="snapshots")
# The React dashboard's Vite dev server calls /api/* from another port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
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


# ---------------------------------------------------------------------------
# Built React dashboard at /app (mounted last so it never shadows a route)
# ---------------------------------------------------------------------------
if (WEB_DIST / "index.html").is_file():
    app.mount("/app", StaticFiles(directory=str(WEB_DIST), html=True), name="app")
