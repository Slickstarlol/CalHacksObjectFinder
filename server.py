"""RecallAI web server: phone GPS relay, search page, Grok agent, alerts.

    uvicorn server:app --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db as dbmod
from agent import RecallAgent, MemoryTools
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
    yield


app = FastAPI(title="RecallAI", lifespan=lifespan)
app.include_router(search_router)
app.mount("/snapshots", StaticFiles(directory=str(SNAPSHOT_DIR)), name="snapshots")


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
