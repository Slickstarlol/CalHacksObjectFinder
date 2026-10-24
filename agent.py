"""RecallAI agent: ask Grok about your belongings in plain language.

    "Where did I leave my mouse?"
    "Was my bag with me at lunch?"
    "What did I have on the hackathon table this morning?"

Grok answers by calling tools over the sightings database (and can look
at the snapshots themselves), then says where to look first and why.

CLI:  python agent.py "where is my mouse?"
API:  POST /api/ask {"question": "...", "history": [...]}   (server.py)
"""

from __future__ import annotations

import copy
import json
import math
import re
from datetime import datetime, timezone

import db as dbmod
import temporal_memory as tm
from llm import (AGENT_MODEL, VISION_MODEL, function_calls, function_tool,
                 get_client, image_part, output_text, text_part)
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
- When useful, look at the photo of the key sighting (look_at_photo) to describe the spot precisely.
- Give: where to look first, why (cite evidence and times), and 1-2 backup places if the evidence is weak
  or old. Say plainly when you don't know (never seen, or seen long ago).
- Cite sightings as [#id] so the app can show their photos. Use relative times ("12 minutes ago") and the
  user's local time. Keep answers short: 2-5 sentences, no headers.

Time questions ("how long since you saw...", "where was it at 10 AM?", "what haven't you seen in an hour?"):
- Tools compute every time and duration. Use their relative_time / absolute_time / seconds_* values as given;
  never work out times or durations yourself.
- "When/how long since": get_last_seen. "Where was X at <time>": get_last_seen with at_time (the latest
  sighting at or before that time; say its actual time and how long before the requested time it was, and
  never use a later sighting as proof of where it was earlier). "Around <time>": add around_minutes.
  "In the last N minutes" / "between": search_sightings. "What haven't you seen in N minutes":
  list_belongings with not_seen_for_minutes (never-seen items are a separate group).
- A sighting shows where the item WAS: say "last seen on the grey table 23 minutes ago", never "it is on the
  table" or "it's in view". Don't call an item lost or forgotten just because it's old. If recording_gap is
  true, the camera stopped recording while the item was still in view.
- No sighting: say so plainly, e.g. "I haven't recorded a sighting of your keys yet." Never fill gaps
  between sightings with guesses about where it went.
- verified "unchecked" means the match wasn't reviewed: say it was probably theirs, not certainly.
- If a name matches several belongings (ambiguous), ask which one or answer for each by full name."""

TOOLS = [
    function_tool(
        "list_belongings",
        "List the user's registered belongings with their most recent sighting "
        "summary. With not_seen_for_minutes, group them into not seen for that "
        "long / seen within it / never seen.",
        {"type": "object",
         "properties": {"not_seen_for_minutes": {"type": "number"}}},
    ),
    function_tool(
        "search_sightings",
        "Search the sighting history, newest first.",
        {
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
                "start_time": {"type": "string",
                               "description": "Only sightings at/after this ISO 8601 time "
                                              "(with the user's UTC offset)"},
                "end_time": {"type": "string",
                             "description": "Only sightings at/before this ISO 8601 time"},
                "limit": {"type": "integer",
                          "description": "Max results (default 15, max 50)"},
            },
        },
    ),
    function_tool(
        "get_last_seen",
        "When and where a belonging was last seen, with the time since already "
        "computed, plus a few earlier sightings. With at_time: the last sighting "
        "at or before that time (historical recall).",
        {
            "type": "object",
            "properties": {
                "belonging": {"type": "string",
                              "description": "Name (partial ok) or id of a belonging"},
                "at_time": {"type": "string",
                            "description": "ISO 8601 time with the user's UTC offset"},
                "around_minutes": {"type": "number",
                                   "description": "With at_time: return the sighting closest "
                                                  "to it within +-N minutes, either side"},
            },
            "required": ["belonging"],
        },
    ),
    function_tool(
        "look_at_photo",
        "Look at the snapshot of a sighting (the item is boxed in green) and "
        "answer a question about it, e.g. 'What surface is it on and what is "
        "right next to it?'",
        {
            "type": "object",
            "properties": {
                "sighting_id": {"type": "integer"},
                "question": {"type": "string"},
            },
            "required": ["sighting_id", "question"],
        },
    ),
    function_tool(
        "get_context",
        "Current local time and the user's live phone GPS (if connected).",
        {"type": "object", "properties": {}},
    ),
    function_tool(
        "list_people",
        "List people the user has saved: name, description, relationship "
        "to the user, and how long they have known them.",
        {"type": "object", "properties": {}},
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_ts(ts):
    return tm.parse_timestamp(ts)


def ago(ts, now=None):
    return tm.format_relative(tm.elapsed(parse_ts(ts), now)) or "at an unknown time"


def local(ts, now=None, tz=None):
    """tz=None: this machine's zone (CLI, watcher). The web agent passes
    the browser's zone."""
    return tm.format_absolute(parse_ts(ts), now, tz) or "unknown time"


def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def summarize(row, gps=None, now=None, tz=None):
    out = {
        "id": row["id"],
        "item": row["belonging_name"] or row["object_name"],
        "event": row["event"],
        "when": f"{local(row['timestamp'], now, tz)} ({ago(row['timestamp'], now)})",
        "where": row["description"] or "(no description yet)",
        "place": row["place"],
        "nearby_objects": row["nearby"] or None,
        "match_score": row["match_score"],
        "ai_verified": {1: "yes", 0: "no"}.get(row["verified"], "unchecked"),
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
                 gps_provider=lambda: None, now=None, vision=None):
        self.db_path = db_path
        self.belongings_dir = belongings_dir
        self.gps_provider = gps_provider
        self.now = now  # fixed "now" for tests
        self.vision = vision  # callable(content_parts) -> text; set by RecallAgent
        self.tz = None  # user's zone for this request (None = this machine's)
        self.referenced = set()
        self.lookups = []  # historical lookups, shown as evidence cards in the UI

    def for_request(self, tz_name=None):
        """Per-request copy, so one user's timezone and citations never leak
        into another request on the shared server agent."""
        tools = copy.copy(self)
        tools.tz = tm.get_timezone(tz_name) if tz_name else self.tz
        tools.referenced, tools.lookups = set(), []
        return tools

    def _now(self):
        return self.now or datetime.now(timezone.utc)

    def run(self, name, args):
        """Run a tool; always returns a string (function_call_output)."""
        try:
            fn = getattr(self, f"tool_{name}")
        except AttributeError:
            return f"Unknown tool {name}"
        try:
            return fn(**(args or {}))
        except Exception as e:
            return f"Tool error: {e}"

    def tool_list_belongings(self, not_seen_for_minutes=None):
        if not_seen_for_minutes is not None:
            inv = tm.get_items_not_seen_since(float(not_seen_for_minutes) * 60, self.db_path,
                                              self._now(), self.belongings_dir, tz=self.tz)
            for group in ("not_seen_since", "seen_within"):
                self.referenced.update(i["sighting_id"] for i in inv[group])
                inv[group] = [_for_model(i) for i in inv[group]]
            return json.dumps(inv, indent=1)
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
                    "last_sighting": summarize(row, self.gps_provider(), self._now(), self.tz)
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
                              since_minutes=None, before_minutes=None,
                              start_time=None, end_time=None, limit=15):
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
            for value, op in ((start_time, ">="), (end_time, "<=")):
                if value:
                    t = tm.parse_timestamp(value)
                    if t is None:
                        return f"Tool error: {value!r} is not an ISO 8601 time."
                    where.append(f"timestamp {op} ?")
                    params.append(tm.to_iso(t))  # stored format, so text order = time order
            limit = max(1, min(int(limit or 15), 50))
            rows = db.execute(
                f"SELECT * FROM sightings WHERE {' AND '.join(where)} "
                "ORDER BY timestamp DESC, id DESC LIMIT ?", (*params, limit)
            ).fetchall()
        if not rows:
            return "No matching sightings."
        gps = self.gps_provider()
        self.referenced.update(r["id"] for r in rows)
        return json.dumps([summarize(r, gps, now, self.tz) for r in rows], indent=1)

    def tool_get_last_seen(self, belonging, at_time=None, around_minutes=None):
        with dbmod.connect(self.db_path) as db:
            ids = self._resolve(db, belonging)
        if not ids:
            return (f'No registered belonging matches "{belonging}". '
                    "Call list_belongings to see the exact names.")
        out = []
        for bid in ids:
            if at_time and around_minutes:
                obs = tm.get_sighting_nearest_time(
                    bid, at_time, float(around_minutes) * 60, self.db_path,
                    self._now(), self.tz)
                if obs is None:
                    obs = {"belonging_id": bid, "requested_at": at_time,
                           "message": f"No sighting within {float(around_minutes):g} "
                                      "minutes of that time."}
            else:
                obs = tm.get_last_seen(bid, self.db_path, self._now(), self.belongings_dir,
                                       history=0 if at_time else 3, at=at_time, tz=self.tz)
            if obs.get("sighting_id"):
                self.referenced.add(obs["sighting_id"])
            if at_time:
                self.lookups.append(obs)
            out.append(_for_model(obs))
        if len(out) > 1:
            return json.dumps({"ambiguous": True, "matches": out}, indent=1)
        return json.dumps(out[0], indent=1)

    def tool_look_at_photo(self, sighting_id,
                           question="Describe exactly where the boxed item is."):
        from interpreter import frame_with_box
        with dbmod.connect(self.db_path) as db:
            row = db.execute("SELECT * FROM sightings WHERE id=?",
                             (int(sighting_id),)).fetchone()
        if row is None:
            return f"No sighting #{sighting_id}"
        self.referenced.add(row["id"])
        if self.vision is None:
            return "Photo viewer unavailable; use the stored description instead."
        name = row["belonging_name"] or row["object_name"]
        content = [image_part(frame_with_box(row["image_path"], row["bbox"]))]
        if row["crop_path"]:
            content.append(image_part(row["crop_path"], max_side=384))
        content.append(text_part(
            f"First image: camera frame; the green box is the user's {name}. "
            + ("Second image: close-up of it. " if row["crop_path"] else "")
            + f"Answer briefly and concretely: {question}"))
        when = local(row["timestamp"], self._now(), self.tz)
        return f"Sighting #{row['id']} ({when}): " + self.vision(content)

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
        now = self._now().astimezone(self.tz)
        return json.dumps({
            "now_local": now.strftime("%A %b %d %Y, %I:%M %p"),
            "now_iso": now.isoformat(),  # carries the UTC offset for at_time/start_time
            "phone_gps": gps or "not connected",
        })


def _minus(now, minutes):
    from datetime import timedelta
    return (now - timedelta(minutes=float(minutes))).isoformat()


# Bookkeeping the model doesn't need; dropped to save tokens.
_HIDDEN = {"image_path", "image_url", "crop_url", "now", "belonging_id", "timestamp",
           "unusable_timestamps"}


def _for_model(d):
    if not isinstance(d, dict):
        return d
    # `is None` checks: 0 seconds / 0.0 scores are real values.
    out = {k: v for k, v in d.items()
           if k not in _HIDDEN and v is not None and v is not False and v != []}
    for k in ("previous", "later_observation"):
        if isinstance(out.get(k), list):
            out[k] = [_for_model(p) for p in out[k]]
        elif isinstance(out.get(k), dict):
            out[k] = _for_model(out[k])
    if d.get("sighting_id") is not None and d.get("verified") is None:
        out["verified"] = "unchecked"
    return out


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

    def ask(self, question, history=None, user_tz=None):
        """history: [{"role": "user"|"assistant", "content": "text"}, ...]
        user_tz: the user's IANA zone name (from the browser); invalid -> UTC."""
        if not self.enabled:
            return {"answer": "Grok isn't configured. Set XAI_API_KEY "
                              "and restart the server.", "sightings": [], "steps": []}
        tools = self.tools.for_request(user_tz)
        if tools.vision is None:
            tools.vision = self._look
        messages = [{"role": "system", "content": SYSTEM}]
        messages += [{"role": m["role"], "content": m["content"]}
                     for m in (history or [])[-10:]]
        messages.append({"role": "user", "content": question})
        steps = []

        # Standard xAI function-calling loop: run every function_call in the
        # output, send the results back chained with previous_response_id.
        response = self.client.responses.create(
            model=self.model, input=messages, tools=TOOLS)
        for _ in range(MAX_TURNS - 1):
            calls = function_calls(response)
            if not calls:
                break
            outputs = []
            for call_id, name, args in calls:
                steps.append({"tool": name, "input": args})
                outputs.append({"type": "function_call_output", "call_id": call_id,
                                "output": tools.run(name, args)})
            response = self.client.responses.create(
                model=self.model, input=outputs, tools=TOOLS,
                previous_response_id=response.id)

        if function_calls(response):
            answer = ("I couldn't finish looking through your memories - "
                      "try a more specific question.")
        else:
            answer = output_text(response) or "Sorry, I couldn't come up with an answer."

        cited = [int(i) for i in re.findall(r"\[#(\d+)\]", answer)]
        out = {"answer": answer, "sightings": self._rows(cited), "steps": steps}
        if tools.lookups:
            out["lookups"] = tools.lookups  # additive: requested vs observed time
        return out

    def _look(self, content):
        """Separate vision request used by the look_at_photo tool."""
        response = self.client.responses.create(
            model=VISION_MODEL, input=[{"role": "user", "content": content}])
        return output_text(response) or "(no description)"

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
