"""RecallAI agent: ask Claude about your belongings in plain language.

    "Where did I leave my mouse?"
    "Was my bag with me at lunch?"
    "What did I have on the hackathon table this morning?"

Claude answers by calling tools over the sightings database (and can look
at the snapshots themselves), then says where to look first and why.

CLI:  python agent.py "where is my mouse?"
API:  POST /api/ask {"question": "...", "history": [...]}   (server.py)
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

import db as dbmod
from claude_client import (AGENT_MODEL, block_to_dict, get_client, image_block,
                           text_of)
from people import list_people
from recognition import BELONGINGS_DIR, load_belongings

MAX_TURNS = 8
VISIBLE = "COALESCE(verified, 1) != 0"

SYSTEM = """You are RecallAI, a personal memory assistant that helps the user find their belongings.
A camera records "sightings" of the user's registered belongings. Each sighting has a time, an event
('seen' = item was in view; 'left_view' = the LAST frame before the item disappeared from view, usually
where it was put down), a description of where it was (written by a vision model from the photo), a place
label, nearby objects, a recognition match score, and sometimes phone GPS.

The user can also save people (name, a short description, their relationship to the user, and how long
they have known them). That list is separate from belongings. Use list_people for questions about a person.

How to answer:
- Always look up the data with tools before answering; never invent sightings or people.
- The most recent 'left_view' sighting is usually the best clue to where an item is now.
- When useful, open the photo of the key sighting (view_sighting_photo) to describe the spot precisely.
- Give: where to look first, why (cite evidence and times), and 1-2 backup places if the evidence is weak
  or old. Say plainly when you don't know (never seen, or seen long ago).
- Cite sightings as [#id] so the app can show their photos. Use relative times ("12 minutes ago") and the
  user's local time. Keep answers short: 2-5 sentences, no headers."""

TOOLS = [
    {
        "name": "list_belongings",
        "description": "List the user's registered belongings with their most "
                       "recent sighting summary.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_sightings",
        "description": "Search the sighting history, newest first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "belonging": {"type": "string",
                              "description": "Name (partial ok) or id of a belonging"},
                "text": {"type": "string",
                         "description": "Match against place/description/nearby objects"},
                "event": {"type": "string", "enum": ["seen", "left_view"]},
                "since_minutes": {"type": "number",
                                  "description": "Only sightings from the last N minutes"},
                "before_minutes": {"type": "number",
                                   "description": "Only sightings older than N minutes"},
                "limit": {"type": "integer", "default": 15, "maximum": 50},
            },
        },
    },
    {
        "name": "view_sighting_photo",
        "description": "Look at the snapshot of a sighting (the item is boxed "
                       "in green on the full frame).",
        "input_schema": {
            "type": "object",
            "properties": {
                "sighting_id": {"type": "integer"},
                "view": {"type": "string", "enum": ["frame", "closeup"],
                         "default": "frame"},
            },
            "required": ["sighting_id"],
        },
    },
    {
        "name": "get_context",
        "description": "Current local time and the user's live phone GPS (if connected).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_people",
        "description": "List people the user has saved: name, description, "
                       "relationship to the user, and how long they have known them.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_ts(ts):
    return datetime.fromisoformat(ts)


def ago(ts, now=None):
    now = now or datetime.now(timezone.utc)
    s = (now - parse_ts(ts)).total_seconds()
    if s < 90:
        return "just now"
    if s < 5400:
        return f"{round(s / 60)} minutes ago"
    if s < 172800:
        return f"{s / 3600:.1f} hours ago"
    return f"{round(s / 86400)} days ago"


def local(ts):
    return parse_ts(ts).astimezone().strftime("%a %b %d %I:%M %p").replace(" 0", " ")


def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def summarize(row, gps=None, now=None):
    out = {
        "id": row["id"],
        "item": row["belonging_name"] or row["object_name"],
        "event": row["event"],
        "when": f"{local(row['timestamp'])} ({ago(row['timestamp'], now)})",
        "where": row["description"] or "(no description yet)",
        "place": row["place"],
        "nearby_objects": row["nearby"] or None,
        "match_score": row["match_score"],
        "claude_verified": {1: "yes", 0: "no"}.get(row["verified"], "unchecked"),
    }
    if row["latitude"] is not None:
        out["gps"] = f"{row['latitude']:.5f},{row['longitude']:.5f} (±{row['accuracy']:.0f} m)"
        if gps:
            out["distance_from_phone_now_m"] = round(distance_m(
                row["latitude"], row["longitude"], gps["latitude"], gps["longitude"]))
    return {k: v for k, v in out.items() if v is not None}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
class MemoryTools:
    def __init__(self, db_path=None, belongings_dir=BELONGINGS_DIR,
                 gps_provider=lambda: None, now=None):
        self.db_path = db_path
        self.belongings_dir = belongings_dir
        self.gps_provider = gps_provider
        self.now = now  # fixed "now" for tests
        self.referenced = set()

    def _now(self):
        return self.now or datetime.now(timezone.utc)

    def run(self, name, args):
        """Returns tool_result content: a string or a list of content blocks."""
        try:
            fn = getattr(self, f"tool_{name}")
        except AttributeError:
            return f"Unknown tool {name}"
        try:
            return fn(**(args or {}))
        except Exception as e:
            return f"Tool error: {e}"

    def tool_list_belongings(self):
        out = []
        with dbmod.connect(self.db_path) as db:
            for b in load_belongings(self.belongings_dir):
                row = db.execute(
                    f"SELECT * FROM sightings WHERE belonging_id=? AND {VISIBLE} "
                    "ORDER BY timestamp DESC, id DESC LIMIT 1", (b.id,)).fetchone()
                n = db.execute(
                    f"SELECT COUNT(*) FROM sightings WHERE belonging_id=? AND {VISIBLE}",
                    (b.id,)).fetchone()[0]
                out.append({
                    "id": b.id, "name": b.name, "kind": b.category,
                    "total_sightings": n,
                    "last_sighting": summarize(row, self.gps_provider(), self._now())
                    if row else "never seen",
                    **({} if b.reference_paths else
                       {"note": "no reference photos registered yet"}),
                })
                if row:
                    self.referenced.add(row["id"])
        return json.dumps(out, indent=1)

    def _resolve(self, db, belonging):
        items = load_belongings(self.belongings_dir)
        q = belonging.strip().lower()
        ids = [b.id for b in items if b.id == q]
        if not ids:
            ids = [b.id for b in items if q in b.name.lower()]
        if not ids:  # "my bag" -> "My Black Backpack": match on words / category
            words = [w for w in re.findall(r"\w+", q) if w not in {"my", "the", "a"}]
            ids = [b.id for b in items
                   if any(w in b.name.lower() or w in b.category for w in words)]
        return ids

    def tool_search_sightings(self, belonging=None, text=None, event=None,
                              since_minutes=None, before_minutes=None, limit=15):
        where, params = [VISIBLE], []
        with dbmod.connect(self.db_path) as db:
            if belonging:
                ids = self._resolve(db, belonging)
                if not ids:
                    return (f'No registered belonging matches "{belonging}". '
                            "Call list_belongings to see the exact names.")
                where.append(f"belonging_id IN ({','.join('?' * len(ids))})")
                params += ids
            else:
                where.append("belonging_id IS NOT NULL")
            if text:
                where.append("(LOWER(COALESCE(description,'')) LIKE ? OR "
                             "LOWER(COALESCE(place,'')) LIKE ? OR "
                             "LOWER(COALESCE(nearby,'')) LIKE ?)")
                params += [f"%{text.lower()}%"] * 3
            if event:
                where.append("event = ?")
                params.append(event)
            now = self._now()
            if since_minutes is not None:
                where.append("timestamp >= ?")
                params.append(_minus(now, since_minutes))
            if before_minutes is not None:
                where.append("timestamp <= ?")
                params.append(_minus(now, before_minutes))
            limit = max(1, min(int(limit or 15), 50))
            rows = db.execute(
                f"SELECT * FROM sightings WHERE {' AND '.join(where)} "
                "ORDER BY timestamp DESC, id DESC LIMIT ?", (*params, limit)
            ).fetchall()
        if not rows:
            return "No matching sightings."
        gps = self.gps_provider()
        self.referenced.update(r["id"] for r in rows)
        return json.dumps([summarize(r, gps, now) for r in rows], indent=1)

    def tool_view_sighting_photo(self, sighting_id, view="frame"):
        from interpreter import frame_with_box
        with dbmod.connect(self.db_path) as db:
            row = db.execute("SELECT * FROM sightings WHERE id=?",
                             (int(sighting_id),)).fetchone()
        if row is None:
            return f"No sighting #{sighting_id}"
        self.referenced.add(row["id"])
        if view == "closeup" and row["crop_path"]:
            img = image_block(row["crop_path"])
        else:
            img = image_block(frame_with_box(row["image_path"], row["bbox"]))
        return [img, {"type": "text",
                      "text": f"Sighting #{row['id']} of {row['belonging_name'] or row['object_name']}"
                              f", {local(row['timestamp'])}"}]

    def tool_list_people(self):
        rows = list_people(self.db_path)
        if not rows:
            return "No people saved yet."
        brief = [{
            "name": p["name"],
            "description": p["description"] or None,
            "relationship": p["relationship"] or None,
            "known_since": p["known_since"],
            "known_for": p["known_for"],
        } for p in rows]
        return json.dumps(brief, indent=1)

    def tool_get_context(self):
        gps = self.gps_provider()
        return json.dumps({
            "now_local": self._now().astimezone().strftime("%A %b %d %Y, %I:%M %p %Z"),
            "phone_gps": gps or "not connected",
        })


def _minus(now, minutes):
    from datetime import timedelta
    return (now - timedelta(minutes=float(minutes))).isoformat()


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------
class RecallAgent:
    def __init__(self, client=None, tools=None, model=AGENT_MODEL):
        self.client = client or get_client()
        self.tools = tools or MemoryTools()
        self.model = model

    @property
    def enabled(self):
        return self.client is not None

    def ask(self, question, history=None):
        """history: [{"role": "user"|"assistant", "content": "text"}, ...]"""
        if not self.enabled:
            return {"answer": "Claude isn't configured. Set ANTHROPIC_API_KEY "
                              "and restart the server.", "sightings": [], "steps": []}
        self.tools.referenced = set()
        messages = [{"role": m["role"], "content": m["content"]}
                    for m in (history or [])[-10:]]
        messages.append({"role": "user", "content": question})
        steps = []

        for _ in range(MAX_TURNS):
            response = self.client.messages.create(
                model=self.model, max_tokens=1024, system=SYSTEM,
                tools=TOOLS, messages=messages,
            )
            messages.append({"role": "assistant",
                             "content": [block_to_dict(b) for b in response.content]})
            calls = [b for b in response.content
                     if getattr(b, "type", None) == "tool_use"]
            if response.stop_reason != "tool_use" or not calls:
                answer = text_of(response)
                break
            results = []
            for call in calls:
                steps.append({"tool": call.name, "input": call.input})
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": self.tools.run(call.name, call.input)})
            messages.append({"role": "user", "content": results})
        else:
            answer = ("I couldn't finish looking through your memories - "
                      "try a more specific question.")

        cited = [int(i) for i in re.findall(r"\[#(\d+)\]", answer)]
        return {"answer": answer, "sightings": self._rows(cited), "steps": steps}

    def _rows(self, ids):
        if not ids:
            return []
        with dbmod.connect(self.tools.db_path) as db:
            rows = db.execute(
                f"SELECT * FROM sightings WHERE id IN ({','.join('?' * len(ids))})",
                ids).fetchall()
        by_id = {r["id"]: dict(r) for r in rows}
        return [by_id[i] for i in dict.fromkeys(ids) if i in by_id]


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Where are my things?"
    result = RecallAgent().ask(q)
    for s in result["steps"]:
        print(f"  [tool] {s['tool']} {s['input']}")
    print(result["answer"])
