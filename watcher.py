"""Proactively flag belongings you may have forgotten.

Every minute (inside server.py):
  1. Rules find candidates:
     - moved_away   : your phone is now far (>150 m) from where the item was
                      last seen  -> you probably walked off without it
     - out_of_sight : the item left the camera's view and hasn't been seen
                      for FORGOT_AFTER_MIN minutes
  2. Claude reviews the candidates with their recent history and decides
     which deserve an alert (and writes a short, specific message). Without
     an API key, a plain template message is used instead.
Each sighting can trigger at most one alert.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import db as dbmod
import temporal_memory as tm
from agent import ago, distance_m, local
from claude_client import AGENT_MODEL, get_client, tool_input
from recognition import BELONGINGS_DIR, load_belongings

FORGOT_AFTER_MIN = 10
MOVED_AWAY_M = 150
VISIBLE = "COALESCE(verified, 1) != 0"

DECIDE_TOOL = {
    "name": "decide_alerts",
    "description": "Decide which candidate items to alert the user about.",
    "input_schema": {
        "type": "object",
        "properties": {
            "alerts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "belonging_id": {"type": "string"},
                        "send": {"type": "boolean",
                                 "description": "false if it's probably fine "
                                                "(e.g. item is clearly left on purpose)"},
                        "urgency": {"type": "string", "enum": ["low", "high"]},
                        "message": {"type": "string",
                                    "description": "1-2 sentences to the user: what, "
                                                   "where it was, when. Specific, not alarmist."},
                    },
                    "required": ["belonging_id", "send", "urgency", "message"],
                },
            }
        },
        "required": ["alerts"],
    },
}

DECIDE_PROMPT = """You are RecallAI's forgotten-item watcher. Below are the user's belongings that simple rules flagged as possibly left behind, with their recent sighting history (newest first).

Rules: "moved_away" = the user's phone is now far from where the item was last seen (strong signal). "out_of_sight" = the item left the camera's view a while ago and hasn't reappeared (weaker: the user may simply have put it away).

Current local time: {now}

Candidates:
{candidates}

For each candidate decide whether to alert. Use high urgency for moved_away or valuables. Messages should tell the user exactly where the item was last seen and when, so they can go get it. Call decide_alerts."""


def find_candidates(db_path=None, gps=None, now=None, belongings_dir=BELONGINGS_DIR,
                    forgot_after_min=FORGOT_AFTER_MIN, moved_away_m=MOVED_AWAY_M):
    now = now or datetime.now(timezone.utc)
    out = []
    with dbmod.connect(db_path) as db:
        for b in load_belongings(belongings_dir):
            last = db.execute(
                f"SELECT * FROM sightings WHERE belonging_id=? AND {VISIBLE} "
                "ORDER BY timestamp DESC, id DESC LIMIT 1", (b.id,)).fetchone()
            if last is None:
                continue
            if db.execute("SELECT 1 FROM alerts WHERE belonging_id=? AND sighting_id=?",
                          (b.id, last["id"])).fetchone():
                continue  # already alerted about this exact sighting

            reason, detail = None, ""
            if gps and last["latitude"] is not None:
                d = distance_m(last["latitude"], last["longitude"],
                               gps["latitude"], gps["longitude"])
                slack = (last["accuracy"] or 0) + (gps.get("accuracy") or 0)
                if d > max(moved_away_m, slack):
                    reason, detail = "moved_away", f"phone is now {d:.0f} m away"
            if reason is None and last["event"] == "left_view":
                age = now - datetime.fromisoformat(last["timestamp"])
                if age >= timedelta(minutes=forgot_after_min):
                    reason = "out_of_sight"
                    detail = f"out of view for {tm.format_duration(age.total_seconds())}"
            if reason is None:
                continue

            history = db.execute(
                f"SELECT * FROM sightings WHERE belonging_id=? AND {VISIBLE} "
                "ORDER BY timestamp DESC, id DESC LIMIT 6", (b.id,)).fetchall()
            out.append({"belonging": b, "last": last, "reason": reason,
                        "detail": detail, "history": history})
    return out


def template_message(c, now=None):
    last = c["last"]
    where = last["description"] or (f"near {last['nearby']}" if last["nearby"]
                                    else "where the camera last saw it")
    name = c["belonging"].name
    if name.lower().startswith("my "):
        name = name[3:]
    seconds = tm.elapsed(tm.parse_timestamp(last["timestamp"]), now)
    return (f"Your {name} hasn't been seen for {tm.format_duration(seconds)}. "
            f"Last seen: {where}.")


def _describe_candidates(cands, now):
    blocks = []
    for c in cands:
        hist = [{"id": h["id"], "event": h["event"],
                 "when": f"{local(h['timestamp'])} ({ago(h['timestamp'], now)})",
                 "where": h["description"], "place": h["place"],
                 "nearby": h["nearby"]} for h in c["history"]]
        blocks.append(json.dumps({
            "belonging_id": c["belonging"].id, "name": c["belonging"].name,
            "kind": c["belonging"].category, "rule": c["reason"],
            "detail": c["detail"], "history": hist}, indent=1))
    return "\n".join(blocks)


def decide(client, cands, now=None, model=AGENT_MODEL):
    """Ask Claude which candidates deserve an alert. {belonging_id: decision}"""
    now = now or datetime.now(timezone.utc)
    if client is None:
        return {c["belonging"].id: {"send": True,
                                    "urgency": "high" if c["reason"] == "moved_away" else "low",
                                    "message": template_message(c, now)} for c in cands}
    response = client.messages.create(
        model=model, max_tokens=800,
        tools=[DECIDE_TOOL], tool_choice={"type": "tool", "name": "decide_alerts"},
        messages=[{"role": "user", "content": DECIDE_PROMPT.format(
            now=now.astimezone().strftime("%a %I:%M %p"),
            candidates=_describe_candidates(cands, now))}],
    )
    result = tool_input(response, "decide_alerts") or {"alerts": []}
    return {a["belonging_id"]: a for a in result.get("alerts", [])}


def run_once(client=None, db_path=None, gps=None, now=None,
             belongings_dir=BELONGINGS_DIR, **rule_kwargs):
    """Check once; store and return new alerts."""
    now = now or datetime.now(timezone.utc)
    cands = find_candidates(db_path, gps, now, belongings_dir, **rule_kwargs)
    if not cands:
        return []
    try:
        decisions = decide(client, cands, now)
    except Exception as e:
        print(f"[WATCHER] Claude unavailable ({e}); using template alerts")
        decisions = decide(None, cands, now)

    new = []
    with dbmod.connect(db_path) as db:
        for c in cands:
            d = decisions.get(c["belonging"].id)
            if not d or not d.get("send"):
                # Remember the decision so we don't ask again for this sighting
                d = {"urgency": "none", "message": "(Claude: no alert needed)",
                     "send": False}
            db.execute(
                "INSERT OR IGNORE INTO alerts (belonging_id, belonging_name, "
                "sighting_id, reason, urgency, message, created_at, dismissed) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (c["belonging"].id, c["belonging"].name, c["last"]["id"],
                 c["reason"], d.get("urgency", "low"), d["message"],
                 now.isoformat(), 0 if d.get("send") else 1))
            if d.get("send"):
                new.append({"belonging": c["belonging"].name, **d})
                print(f"[ALERT] {d['message']}")
        db.commit()
    return new


class Watcher:
    """Background thread for server.py."""

    def __init__(self, gps_provider, interval=60, client=None):
        self.gps_provider = gps_provider
        self.interval = interval
        self.client = client if client is not None else get_client()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                run_once(self.client, gps=self.gps_provider())
            except Exception as e:
                print(f"[WATCHER] error: {e}")
            time.sleep(self.interval)
