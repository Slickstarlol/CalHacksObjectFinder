"""Temporal memory: when was each belonging seen, and how long ago?

Every answer is computed from rows in the existing sightings table, never
guessed. The agent (agent.py), search API (search.py) and watcher share
these helpers so they all agree on what a timestamp means.

    get_last_seen("mouse1")                     latest observation + age
    get_last_seen("mouse1", at=t)               latest observation at or before t
    get_sighting_nearest_time("mouse1", t, w)   closest observation within +-w of t
    get_sightings_between(start, end)           observations in [start, end]
    get_items_not_seen_since(1800)              inventory split by recency

Semantics:
  * Timestamps are written by recorder.py as datetime.now(timezone.utc)
    .isoformat(), so they share one format and SQLite's text order is real
    time order. Queries keep using ORDER BY timestamp; Python only parses
    the rows it returns. Naive values are read as UTC because the schema
    has always stored UTC.
  * 'seen' and 'left_view' rows both carry the time of a frame in which the
    item was visible ('left_view' = the LAST such frame). So last seen =
    newest row of either kind; it is never the moment the item went missing.
  * A sighting is where the item WAS. Nothing here says where it is now.
  * Rejected sightings (verified = 0) are hidden, as everywhere else;
    unreviewed ones (verified NULL) count but are reported as unverified.
  * Read-only: looking an item up never creates a sighting or resets its age.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from recallai import paths
from recallai.memory import db as dbmod
from recallai.vision.recognition import BELONGINGS_DIR, load_belongings

# Stored image paths ("snapshots/x.jpg") are relative to the data folder.
BASE_DIR = paths.DATA_DIR
VISIBLE = "COALESCE(verified, 1) != 0"

# Rows stamped slightly ahead of "now" are clock jitter (age clamped to 0);
# further ahead they are bad data and skipped.
FUTURE_TOLERANCE = timedelta(minutes=2)
JUST_NOW_SECONDS = 10
# The recorder saves a 'seen' row every ~15 s while an item is in view and
# a 'left_view' row ~5 s after it leaves. A newest row that is 'seen' and
# older than this means recording stopped while the item was still in view
# (camera off, app closed) - not that the item left.
RECORDING_GAP_SECONDS = 60


def utcnow():
    """The clock. Tests monkeypatch this or pass now=... explicitly."""
    return datetime.now(timezone.utc)


def _now(now=None):
    if callable(now):
        now = now()
    now = now or utcnow()
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def get_timezone(name):
    """Validated IANA zone (e.g. from the browser), else UTC."""
    if not name:
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(str(name))
    except Exception:  # unknown name, or no tz database on this machine
        return timezone.utc


# ---------------------------------------------------------------------------
# Timestamps and formatting
# ---------------------------------------------------------------------------
def parse_timestamp(value):
    """Stored/requested timestamp -> timezone-aware UTC datetime, or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text[-1:] in ("z", "Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso(dt):
    """Same format recorder.py writes, so it compares correctly in SQL."""
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def elapsed(dt, now=None):
    """Seconds from dt to now; tiny clock skew -> 0; None if dt is missing
    or too far in the future to trust."""
    if dt is None:
        return None
    s = (_now(now) - dt).total_seconds()
    if s < -FUTURE_TOLERANCE.total_seconds():
        return None
    return max(0.0, s)


def _plural(n, unit):
    return f"{n} {unit}{'' if n == 1 else 's'}"


def format_duration(seconds):
    """"45 seconds" / "1 hour 12 minutes" / "2 days". Floored, so it never
    overstates. None for missing or negative input."""
    if seconds is None or seconds < 0:
        return None
    s = int(seconds)
    if s < 60:
        return _plural(s, "second")
    if s < 3600:
        return _plural(s // 60, "minute")
    if s < 86400:
        h, m = divmod(s // 60, 60)
        return _plural(h, "hour") + (" " + _plural(m, "minute") if m else "")
    return _plural(s // 86400, "day")


def format_relative(seconds):
    """"just now" (< 10 s) / "45 seconds ago" / "1 hour 12 minutes ago"."""
    if seconds is None or seconds < 0:
        return None
    if seconds < JUST_NOW_SECONDS:
        return "just now"
    return format_duration(seconds) + " ago"


def format_clock(dt):
    h = dt.hour % 12 or 12   # strftime's %-I isn't portable to Windows
    return f"{h}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def format_absolute(dt, now=None, tz=None):
    """"today at 11:42 AM" / "yesterday at 9:05 PM" / "Tue Oct 20 at 3:15 PM"
    in zone tz (None = this machine's zone)."""
    if dt is None:
        return None
    local = dt.astimezone(tz)
    today = _now(now).astimezone(tz).date()
    day = local.date()
    if day == today:
        label = "today"
    elif day == today - timedelta(days=1):
        label = "yesterday"
    elif day.year == today.year:
        label = local.strftime("%a %b ") + str(local.day)
    else:
        label = local.strftime("%b ") + f"{local.day}, {local.year}"
    return f"{label} at {format_clock(local)}"


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------
def media_url(path, base_dir=None):
    """URL under server.py's /snapshots mount, or None if the file is
    missing or the path points outside snapshots/."""
    if not path:
        return None
    base = Path(base_dir or BASE_DIR)
    rel = PurePosixPath(str(path).replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts or not rel.parts \
            or rel.parts[0] != "snapshots":
        return None
    full = base / Path(*rel.parts)
    return "/" + rel.as_posix() if full.is_file() else None


# ---------------------------------------------------------------------------
# Rows -> observations
# ---------------------------------------------------------------------------
def observation(row, now=None, tz=None, base_dir=None):
    """One sightings row as structured evidence. Missing data stays None;
    the stored timestamp is returned unchanged next to its parsed form."""
    at = parse_timestamp(row["timestamp"])
    secs = elapsed(at, now)
    return {
        "sighting_id": row["id"],
        "belonging_id": row["belonging_id"],
        "belonging_name": row["belonging_name"] or row["object_name"],
        "event": row["event"],
        "timestamp": row["timestamp"],
        "seen_at": to_iso(at),
        "seconds_since": None if secs is None else round(secs, 1),
        "relative_time": format_relative(secs),
        "absolute_time": format_absolute(at, now, tz) if secs is not None else None,
        "location_description": row["description"] or None,
        "place": row["place"] or None,
        "nearby_objects": [n for n in (row["nearby"] or "").split(", ") if n],
        "image_path": row["image_path"],
        "image_url": media_url(row["image_path"], base_dir),
        "crop_url": media_url(row["crop_path"], base_dir),
        "confidence": row["confidence"],
        "match_score": row["match_score"],
        "verified": {1: True, 0: False}.get(row["verified"]),
    }


def _rows(db, where, params, now, newest_first=True):
    """Visible rows matching `where`, in time order, lazily, skipping rows
    whose timestamp is unparseable or in the future. Ties: higher id is
    treated as newer. Yields (datetime, row)."""
    order = "DESC" if newest_first else "ASC"
    cutoff = _now(now) + FUTURE_TOLERANCE
    cur = db.execute(
        f"SELECT * FROM sightings WHERE {VISIBLE} AND ({where}) "
        f"AND timestamp <= ? ORDER BY timestamp {order}, id {order}",
        (*params, to_iso(cutoff)))
    for r in cur:
        at = parse_timestamp(r["timestamp"])
        if at is not None and at <= cutoff:
            yield at, r


def _unusable(db, belonging_id, now):
    """Rows hidden from temporal answers because their timestamp is in the
    future or not a timestamp (text sorts after the cutoff)."""
    return db.execute(
        f"SELECT COUNT(*) FROM sightings WHERE {VISIBLE} AND belonging_id = ? "
        "AND NOT (timestamp <= ?)",
        (belonging_id, to_iso(_now(now) + FUTURE_TOLERANCE))).fetchone()[0]


def _your(name):
    if not name:
        return "that item"
    return "your " + name[3:] if name.lower().startswith("my ") else name


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_last_seen(belonging_id, db_path=None, now=None, belongings_dir=BELONGINGS_DIR,
                  history=0, at=None, tz=None, base_dir=None, name=None):
    """Latest observation of one belonging (by stable id).

    at:      only consider observations at or before this time ("where was it
             at 10 AM?"); the first observation after it is returned
             separately as 'later_observation', clearly labelled.
    history: include up to N earlier observations as 'previous'.

    status: 'never_seen'      no usable observation (or none before `at`)
            'seen'            newest row is a periodic in-view snapshot
            'left_view'       newest row is the last frame before it left view
            'unavailable'     only rows with invalid/future timestamps exist
    """
    now = _now(now)
    target = parse_timestamp(at) if at is not None else None
    if at is not None and target is None:
        raise ValueError(f"Not an ISO 8601 time: {at!r}")
    if name is None:
        name = next((b.name for b in load_belongings(belongings_dir)
                     if b.id == belonging_id), None)

    with dbmod.connect(db_path) as db:
        where, params = "belonging_id = ?", [belonging_id]
        if target is not None:
            where += " AND timestamp <= ?"
            params.append(to_iso(target))
        found = []
        for at_row in _rows(db, where, params, now):
            if not found or (at_row[0], at_row[1]["image_path"]) != \
                    (found[-1][0], found[-1][1]["image_path"]):  # same frame twice
                found.append(at_row)
            if len(found) > history:
                break
        total = db.execute(
            f"SELECT COUNT(*) FROM sightings WHERE {VISIBLE} AND belonging_id = ?",
            (belonging_id,)).fetchone()[0]
        unusable = _unusable(db, belonging_id, now)
        cutoff = now + FUTURE_TOLERANCE if target is None else min(target, now)
        last_verified = db.execute(
            "SELECT MAX(timestamp) FROM sightings WHERE belonging_id = ? "
            "AND verified = 1 AND timestamp <= ?",
            (belonging_id, to_iso(cutoff))).fetchone()[0]
        later = None
        if target is not None:
            later = next(_rows(db, "belonging_id = ? AND timestamp > ?",
                               (belonging_id, to_iso(target)), now,
                               newest_first=False), None)

    out = {
        "belonging_id": belonging_id,
        "belonging_name": name or (found[0][1]["belonging_name"] if found else None),
        "now": to_iso(now),
        "status": "never_seen",
        # Same keys whether or not there's a sighting, so callers can rely on them.
        "last_seen_at": None, "seconds_since": None, "relative_time": None,
        "absolute_time": None, "sighting_id": None, "event": None,
        "location_description": None, "image_url": None, "crop_url": None,
        "verified": None,
        "total_sightings": total,
        "unusable_timestamps": unusable,
    }
    if target is not None:
        out["requested_at"] = to_iso(target)
        out["later_observation"] = (
            {**observation(later[1], now, tz, base_dir),
             "seconds_after_requested": round((later[0] - target).total_seconds(), 1)}
            if later else None)
    if not found:
        if total and total == unusable:
            out["status"] = "unavailable"
            out["message"] = (f"I have records of {_your(out['belonging_name'])}, "
                              "but their timestamps are invalid, so I can't say when.")
        elif target is not None:
            out["message"] = (f"I have no recorded sighting of "
                              f"{_your(out['belonging_name'])} at or before that time.")
        else:
            out["message"] = (f"I haven't recorded a sighting of "
                              f"{_your(out['belonging_name'])} yet.")
        return out

    last = observation(found[0][1], now, tz, base_dir)
    out.update({k: v for k, v in last.items()
                if k not in ("belonging_id", "belonging_name", "seen_at")})
    out["last_seen_at"] = last["seen_at"]
    out["status"] = "left_view" if last["event"] == "left_view" else "seen"
    out["last_verified_seen_at"] = to_iso(parse_timestamp(last_verified))
    if target is not None:
        out["seconds_before_requested"] = round((target - found[0][0]).total_seconds(), 1)
    elif last["event"] == "seen" and (last["seconds_since"] or 0) > RECORDING_GAP_SECONDS:
        # Still in view at its last frame, and nothing recorded since.
        out["recording_gap"] = True
    if history:
        out["previous"] = [observation(r, now, tz, base_dir) for _, r in found[1:]]
    return out


def get_sighting_nearest_time(belonging_id, at, window_seconds=900, db_path=None,
                              now=None, tz=None, base_dir=None):
    """Observation closest to `at` within +-window ("around 10:30"), with
    the signed gap (negative = before the requested time). None if none."""
    target = parse_timestamp(at)
    if target is None:
        return None
    lo, hi = target - timedelta(seconds=window_seconds), target + timedelta(seconds=window_seconds)
    with dbmod.connect(db_path) as db:
        pairs = list(_rows(db, "belonging_id = ? AND timestamp >= ? AND timestamp <= ?",
                           (belonging_id, to_iso(lo), to_iso(hi)), now))
    if not pairs:
        return None
    # Newest first, and min() keeps the first of equal gaps: ties -> later one.
    t, row = min(pairs, key=lambda p: abs((p[0] - target).total_seconds()))
    out = observation(row, now, tz, base_dir)
    out["requested_at"] = to_iso(target)
    out["offset_seconds"] = round((t - target).total_seconds(), 1)
    return out


def get_sightings_between(start=None, end=None, belonging_id=None, db_path=None,
                          now=None, event=None, limit=50, tz=None, base_dir=None):
    """Observations with start <= time <= end (both inclusive), newest first.
    end defaults to now. belonging_id: one id, a list of ids, or None for
    every registered belonging."""
    now = _now(now)
    start = parse_timestamp(start) if start is not None else None
    end = parse_timestamp(end) if end is not None else now
    if end is None or (start is not None and start > end):
        return []
    where, params = ["belonging_id IS NOT NULL"], []
    if belonging_id:
        ids = [belonging_id] if isinstance(belonging_id, str) else list(belonging_id)
        where.append(f"belonging_id IN ({','.join('?' * len(ids))})")
        params += ids
    if start is not None:
        where.append("timestamp >= ?")
        params.append(to_iso(start))
    where.append("timestamp <= ?")
    params.append(to_iso(end))
    if event:
        where.append("event = ?")
        params.append(event)
    out = []
    with dbmod.connect(db_path) as db:
        for _, r in _rows(db, " AND ".join(where), params, now):
            out.append(observation(r, now, tz, base_dir))
            if len(out) >= limit:
                break
    return out


def get_items_not_seen_since(threshold, db_path=None, now=None,
                             belongings_dir=BELONGINGS_DIR, tz=None, base_dir=None):
    """Inventory against a threshold (seconds or timedelta).

    Returns {"not_seen_since": [...] longest-unseen first (age > threshold),
             "seen_within":    [...] most recent first,
             "never_seen":     [...], "unavailable": [...]}.
    Never-seen items are their own category, not "seen long ago".
    One query for every belonging's latest row (window function).
    """
    now = _now(now)
    secs = threshold.total_seconds() if isinstance(threshold, timedelta) else float(threshold)
    belongings = [b for b in load_belongings(belongings_dir) if b.tracking]
    with dbmod.connect(db_path) as db:
        latest = {r["belonging_id"]: r for r in db.execute(
            f"""SELECT * FROM (
                  SELECT *, ROW_NUMBER() OVER (PARTITION BY belonging_id
                                               ORDER BY timestamp DESC, id DESC) AS rn
                  FROM sightings
                  WHERE {VISIBLE} AND belonging_id IS NOT NULL AND timestamp <= ?)
                WHERE rn = 1""", (to_iso(now + FUTURE_TOLERANCE),))}
    out = {"threshold_seconds": secs, "threshold": format_duration(secs),
           "not_seen_since": [], "seen_within": [], "never_seen": [], "unavailable": []}
    for b in belongings:
        row = latest.get(b.id)
        if row is None or parse_timestamp(row["timestamp"]) is None:
            # Rare: let the careful per-item lookup classify it.
            info = get_last_seen(b.id, db_path, now, belongings_dir, tz=tz,
                                 base_dir=base_dir, name=b.name)
        else:
            obs = observation(row, now, tz, base_dir)
            info = {**obs, "belonging_name": b.name, "last_seen_at": obs["seen_at"],
                    "status": "left_view" if row["event"] == "left_view" else "seen"}
        if info["status"] in ("never_seen", "unavailable"):
            out[info["status"]].append({"belonging_id": b.id, "belonging_name": b.name})
        elif info["seconds_since"] > secs:
            out["not_seen_since"].append(info)
        else:
            out["seen_within"].append(info)
    out["not_seen_since"].sort(key=lambda x: -x["seconds_since"])
    out["seen_within"].sort(key=lambda x: x["seconds_since"])
    return out
