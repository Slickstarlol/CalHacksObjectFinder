"""Temporal memory: last-seen, historical recall and inventory, plus how the
agent, API and watcher use them. Every test runs on a frozen clock against
a temporary SQLite database (no camera, Grok key or network needed)."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

import agent as agentmod
import db as dbmod
import temporal_memory as tm
import watcher
from conftest import RED, make_object_image, register
from recognition import BelongingMatcher, HistogramEmbedder
from recorder import Detection, SightingRecorder
from test_grok_features import FakeClient, fcall, message, reply

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)


def ago(**kw):
    """A timestamp `kw` before NOW, in the format recorder.py writes."""
    return (NOW - timedelta(**kw)).isoformat()


@pytest.fixture
def mem(tmp_path, belongings_dir, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "m.db")
    monkeypatch.setattr(tm, "BASE_DIR", tmp_path)
    monkeypatch.setattr(tm, "utcnow", lambda: NOW)
    dbmod.init_db()
    for name, cat, bid in [("My Mouse", "mouse", "mouse1"), ("My Keys", "keys", "keys1"),
                           ("My Bag", "backpack", "bag1"), ("My AirPods", "earbuds", "pods1")]:
        register(belongings_dir, name, cat, [make_object_image(RED, seed=len(bid))],
                 item_id=bid)
    (tmp_path / "snapshots" / "crops").mkdir(parents=True)

    def add(bid="mouse1", name=None, **kw):
        base = dict(object_name="mouse", timestamp=NOW.isoformat(),
                    image_path="snapshots/x.jpg", belonging_id=bid,
                    belonging_name=name or {"mouse1": "My Mouse", "keys1": "My Keys",
                                            "bag1": "My Bag", "pods1": "My AirPods"}.get(bid, bid),
                    event="seen", match_score=0.9, confidence=0.8)
        base.update(kw)
        with dbmod.connect() as db:
            return dbmod.insert_sighting(db, **base)

    return SimpleNamespace(dir=belongings_dir, add=add, tmp=tmp_path)


def last(mem, bid="mouse1", **kw):
    return tm.get_last_seen(bid, now=NOW, belongings_dir=mem.dir, **kw)


def tools_for(mem, **kw):
    return agentmod.MemoryTools(belongings_dir=mem.dir, now=NOW, **kw)


# ======================================================== relative time
@pytest.mark.parametrize("secs,label", [
    (0, "just now"), (9.9, "just now"), (10, "10 seconds ago"), (30, "30 seconds ago"),
    (59, "59 seconds ago"), (59.99, "59 seconds ago"), (60, "1 minute ago"),
    (300, "5 minutes ago"), (3599, "59 minutes ago"), (3600, "1 hour ago"),
    (3600 + 12 * 60, "1 hour 12 minutes ago"), (7260, "2 hours 1 minute ago"),
    (86399, "23 hours 59 minutes ago"), (86400, "1 day ago"),
    (3 * 86400 + 5000, "3 days ago"), (None, None), (-5, None)])
def test_relative_time(secs, label):
    assert tm.format_relative(secs) == label


def test_absolute_time_labels():
    utc = timezone.utc
    assert tm.format_absolute(NOW.replace(hour=11, minute=42), NOW, utc) == "today at 11:42 AM"
    assert tm.format_absolute(NOW - timedelta(days=1), NOW, utc) == "yesterday at 6:00 PM"
    assert tm.format_absolute(NOW - timedelta(days=4), NOW, utc) == "Tue Oct 20 at 6:00 PM"
    assert tm.format_absolute(None, NOW) is None


# ======================================================== timestamp correctness
@pytest.mark.parametrize("raw", [
    "2026-10-24T18:00:00+00:00", "2026-10-24T18:00:00.000000+00:00",
    "2026-10-24T18:00:00Z", "2026-10-24T11:00:00-07:00", "2026-10-25T03:00:00+09:00",
    "2026-10-24T18:00:00"])  # naive: the schema has always stored UTC
def test_parse_timestamp_formats(raw):
    assert tm.parse_timestamp(raw) == NOW


@pytest.mark.parametrize("raw", [None, "", "yesterday", "2026-13-45T00:00:00", True])
def test_parse_timestamp_invalid(raw):
    assert tm.parse_timestamp(raw) is None


def tz_or_skip(name):
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        return zoneinfo.ZoneInfo(name)
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no tz database (pip install tzdata)")


def test_dst_change_uses_real_elapsed_time():
    la = tz_or_skip("America/Los_Angeles")
    # US DST ends 2026-11-01 02:00 PDT, so 1:30 AM happens twice.
    seen = datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)   # 1:30 AM PDT
    now = datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc)    # 1:30 AM PST
    assert tm.format_relative(tm.elapsed(seen, now)) == "1 hour ago"
    assert tm.format_absolute(seen, now, la) == "today at 1:30 AM"


def test_timezone_validation():
    assert tm.get_timezone(None) is timezone.utc
    assert tm.get_timezone("Not/AZone") is timezone.utc
    assert tm.get_timezone("../../etc/passwd") is timezone.utc
    assert str(tm.get_timezone("America/New_York")) in ("America/New_York", "UTC")


def test_clock_injection(mem):
    mem.add(timestamp=ago(hours=1, minutes=12, seconds=30))
    assert last(mem)["seconds_since"] == 4350
    assert last(mem)["relative_time"] == "1 hour 12 minutes ago"
    later = tm.get_last_seen("mouse1", now=lambda: NOW + timedelta(days=2),
                             belongings_dir=mem.dir)
    assert later["relative_time"] == "2 days ago"
    # the stored timestamp is returned unchanged
    assert later["timestamp"] == ago(hours=1, minutes=12, seconds=30)


def test_future_timestamps(mem):
    mem.add(timestamp=ago(minutes=20))
    mem.add(timestamp=(NOW + timedelta(days=3)).isoformat())   # clock bug: ignored
    r = last(mem)
    assert r["seconds_since"] == 1200 and r["unusable_timestamps"] == 1
    mem.add(timestamp=(NOW + timedelta(seconds=30)).isoformat())  # jitter: clamped
    r = last(mem)
    assert r["seconds_since"] == 0 and r["relative_time"] == "just now"


def test_invalid_timestamps(mem):
    mem.add("keys1", timestamp="not a date")
    r = last(mem, "keys1")
    assert r["status"] == "unavailable" and r["last_seen_at"] is None
    assert "timestamps are invalid" in r["message"]
    mem.add(timestamp="garbage")
    mem.add(timestamp=ago(minutes=3))
    assert last(mem)["seconds_since"] == 180


# ======================================================== last-seen semantics
def test_single_sighting_structured(mem):
    (mem.tmp / "snapshots" / "b.jpg").write_bytes(b"jpg")
    sid = mem.add(timestamp=ago(minutes=23), image_path="snapshots/b.jpg",
                  crop_path="snapshots/crops/missing.jpg", description="On the grey table",
                  place="desk", nearby="laptop, cup", verified=1)
    r = last(mem)
    assert r["status"] == "seen" and r["sighting_id"] == sid
    assert r["belonging_name"] == "My Mouse" and r["last_seen_at"] == ago(minutes=23)
    assert r["seconds_since"] == 1380 and r["relative_time"] == "23 minutes ago"
    assert r["location_description"] == "On the grey table"
    assert r["nearby_objects"] == ["laptop", "cup"]
    assert r["image_url"] == "/snapshots/b.jpg"
    assert r["crop_url"] is None            # missing file -> no fake image
    assert r["confidence"] == 0.8 and r["verified"] is True


def test_latest_of_many_and_ties(mem):
    mem.add(timestamp=ago(hours=2), description="In the drawer")
    a = mem.add(timestamp=ago(minutes=10), image_path="snapshots/a.jpg")
    b = mem.add(timestamp=ago(minutes=10), image_path="snapshots/b.jpg")
    r = last(mem, history=5)
    assert r["sighting_id"] == b > a     # equal times: later row wins
    assert [p["sighting_id"] for p in r["previous"]][0] == a
    assert r["previous"][-1]["location_description"] == "In the drawer"


def test_duplicate_frame_collapsed(mem):
    mem.add(timestamp=ago(minutes=10))
    mem.add(timestamp=ago(minutes=10))   # same frame stored twice
    mem.add(timestamp=ago(minutes=30))
    assert len(last(mem, history=5)["previous"]) == 1


def test_seen_then_left_view(mem):
    mem.add(timestamp=ago(minutes=30))
    sid = mem.add(timestamp=ago(minutes=29), event="left_view", description="On the desk")
    r = last(mem)
    assert r["status"] == "left_view" and r["sighting_id"] == sid
    assert r["seconds_since"] == 29 * 60 and "recording_gap" not in r


def recorder_world(tmp_path, belongings_dir, monkeypatch):
    import recorder
    register(belongings_dir, "My Bag", "backpack",
             [make_object_image(RED, seed=i) for i in range(3)])
    clock = {"t": NOW}

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["t"]
    monkeypatch.setattr(recorder, "datetime", FakeDT)
    rec = SightingRecorder(BelongingMatcher(HistogramEmbedder(), belongings_dir),
                           db_path=tmp_path / "r.db", snapshot_dir=tmp_path / "s",
                           save_interval=15, gone_after=5)
    frame = np.full((480, 640, 3), 90, np.uint8)
    frame[150:270, 40:200] = make_object_image(RED, seed=50)
    return rec, clock, frame, [Detection("backpack", 0.9, (40, 150, 200, 270))]


def test_recorder_left_view_written_with_last_visible_time(tmp_path, belongings_dir,
                                                         monkeypatch):
    rec, clock, frame, det = recorder_world(tmp_path, belongings_dir, monkeypatch)
    rec.process(frame, det, now=0)                       # 'seen' row @ NOW
    clock["t"] = NOW + timedelta(seconds=3)
    rec.process(frame, det, now=3)                       # visible, not saved
    clock["t"] = NOW + timedelta(seconds=60)
    rec.process(np.full_like(frame, 90), [], now=60)     # noticed gone -> new row
    rec.close()
    r = tm.get_last_seen("my_bag", db_path=tmp_path / "r.db",
                         now=NOW + timedelta(seconds=60), belongings_dir=belongings_dir)
    assert r["status"] == "left_view" and r["total_sightings"] == 2
    assert r["last_seen_at"] == (NOW + timedelta(seconds=3)).isoformat()  # not +60 s
    assert r["seconds_since"] == 57


def test_recorder_left_view_relabelled_from_seen(tmp_path, belongings_dir, monkeypatch):
    rec, clock, frame, det = recorder_world(tmp_path, belongings_dir, monkeypatch)
    rec.process(frame, det, now=0)                       # saved; also the final frame
    clock["t"] = NOW + timedelta(seconds=30)
    rec.process(np.full_like(frame, 90), [], now=30)     # relabels that row
    rec.close()
    r = tm.get_last_seen("my_bag", db_path=tmp_path / "r.db",
                         now=NOW + timedelta(seconds=30), belongings_dir=belongings_dir)
    assert r["status"] == "left_view" and r["total_sightings"] == 1
    assert r["last_seen_at"] == NOW.isoformat() and r["seconds_since"] == 30


def test_rejected_excluded_unverified_flagged(mem):
    mem.add(timestamp=ago(minutes=50), verified=1)
    mem.add(timestamp=ago(minutes=5), verified=0)        # Grok: not yours
    mem.add(timestamp=ago(minutes=20))                   # not reviewed yet
    r = last(mem)
    assert r["seconds_since"] == 1200 and r["verified"] is None
    assert r["last_verified_seen_at"] == ago(minutes=50)
    assert r["total_sightings"] == 2


def test_no_observation(mem):
    r = last(mem, "keys1")
    assert r["status"] == "never_seen" and r["last_seen_at"] is None
    assert r["relative_time"] is None and r["image_url"] is None
    assert r["message"] == "I haven't recorded a sighting of your Keys yet."


def test_recording_gap_when_camera_stops_mid_view(mem):
    mem.add(timestamp=ago(minutes=12))                   # last row 'seen', no left_view
    assert last(mem)["recording_gap"] is True
    mem.add("bag1", timestamp=ago(seconds=20))           # fresh: no gap claimed
    assert "recording_gap" not in last(mem, "bag1")


# ======================================================== historical recall
@pytest.fixture
def day(mem):
    """The spec's example: 09:52 desk, 10:08 laptop, 10:31 left view (UTC)."""
    at = lambda h, m: NOW.replace(hour=h, minute=m).isoformat()  # noqa: E731
    return {
        "desk": mem.add(timestamp=at(9, 52), description="On the desk"),
        "laptop": mem.add(timestamp=at(10, 8), description="Near the laptop"),
        "left": mem.add(timestamp=at(10, 31), event="left_view", description="Near the laptop"),
        "at": at,
    }


def test_at_or_before_never_uses_later_sighting(mem, day):
    r = last(mem, at=day["at"](10, 0))
    assert r["sighting_id"] == day["desk"]
    assert r["location_description"] == "On the desk"
    assert r["seconds_before_requested"] == 8 * 60
    assert r["requested_at"] == day["at"](10, 0)
    later = r["later_observation"]
    assert later["sighting_id"] == day["laptop"] and later["seconds_after_requested"] == 480


def test_exact_timestamp_counts_as_at(mem, day):
    r = last(mem, at=day["at"](10, 8))
    assert r["sighting_id"] == day["laptop"] and r["seconds_before_requested"] == 0


def test_no_earlier_observation(mem, day):
    r = last(mem, at=day["at"](9, 0))
    assert r["status"] == "never_seen" and r["sighting_id"] is None
    assert "at or before that time" in r["message"]
    assert r["later_observation"]["sighting_id"] == day["desk"]


def test_historical_with_offset_across_midnight_utc(mem):
    # 23:30 on Oct 23 in Los Angeles is 06:30 UTC on Oct 24.
    early = mem.add(timestamp="2026-10-24T06:00:00+00:00")
    mem.add(timestamp="2026-10-24T07:00:00+00:00")
    r = last(mem, at="2026-10-23T23:30:00-07:00")
    assert r["sighting_id"] == early and r["seconds_before_requested"] == 1800


def test_around_window(mem, day):
    near = tm.get_sighting_nearest_time("mouse1", day["at"](10, 5), 15 * 60, now=NOW)
    assert near["sighting_id"] == day["laptop"] and near["offset_seconds"] == 180
    # 10:08 is 9 min from 9:59; window of 8 min sees only 9:52 (7 min)
    near = tm.get_sighting_nearest_time("mouse1", day["at"](9, 59), 8 * 60, now=NOW)
    assert near["sighting_id"] == day["desk"] and near["offset_seconds"] == -420
    # just outside the window
    assert tm.get_sighting_nearest_time("mouse1", day["at"](9, 40), 11 * 60, now=NOW) is None


def test_windows(mem, day):
    win = tm.get_sightings_between(day["at"](9, 52), day["at"](10, 8), "mouse1", now=NOW)
    assert [s["sighting_id"] for s in win] == [day["laptop"], day["desk"]]   # inclusive
    assert tm.get_sightings_between(day["at"](11, 0), day["at"](12, 0), now=NOW) == []
    assert tm.get_sightings_between(day["at"](12, 0), day["at"](11, 0), now=NOW) == []
    places = {s["location_description"] for s in
              tm.get_sightings_between(None, None, "mouse1", now=NOW)}
    assert places == {"On the desk", "Near the laptop"}       # same item, two places


def test_window_respects_belonging_id(mem, day):
    mem.add("bag1", timestamp=day["at"](10, 0))
    ids = {s["belonging_id"] for s in
           tm.get_sightings_between(day["at"](9, 0), day["at"](11, 0), now=NOW)}
    assert ids == {"mouse1", "bag1"}
    only = tm.get_sightings_between(day["at"](9, 0), day["at"](11, 0), "bag1", now=NOW)
    assert [s["belonging_id"] for s in only] == ["bag1"]


# ======================================================== inventory
def test_inventory_example_from_spec(mem):
    # At 2:30 PM: mouse 2:25, AirPods 1:40, backpack 12:55, keys never.
    now = NOW.replace(hour=14, minute=30)
    mem.add(timestamp=now.replace(hour=14, minute=25).isoformat())
    mem.add("pods1", timestamp=now.replace(hour=13, minute=40).isoformat())
    mem.add("bag1", timestamp=now.replace(hour=12, minute=55).isoformat())
    inv = tm.get_items_not_seen_since(30 * 60, now=now, belongings_dir=mem.dir)
    assert [i["belonging_name"] for i in inv["not_seen_since"]] == ["My Bag", "My AirPods"]
    assert [i["belonging_name"] for i in inv["seen_within"]] == ["My Mouse"]
    assert [i["belonging_name"] for i in inv["never_seen"]] == ["My Keys"]
    assert inv["not_seen_since"][1]["relative_time"] == "50 minutes ago"
    assert inv["threshold"] == "30 minutes"


def test_inventory_boundaries_and_bad_rows(mem):
    mem.add(timestamp=ago(minutes=5))
    mem.add("bag1", timestamp=ago(hours=3))
    mem.add("pods1", timestamp=ago(minutes=50))
    mem.add("keys1", timestamp="garbage")
    inv = tm.get_items_not_seen_since(timedelta(minutes=50), now=NOW, belongings_dir=mem.dir)
    assert [i["belonging_id"] for i in inv["not_seen_since"]] == ["bag1"]   # strictly older
    assert [i["belonging_id"] for i in inv["seen_within"]] == ["mouse1", "pods1"]
    assert [i["belonging_id"] for i in inv["unavailable"]] == ["keys1"]


def test_inventory_uses_latest_row_and_tie_break(mem):
    mem.add(timestamp=ago(hours=2))
    a = mem.add(timestamp=ago(minutes=5), image_path="snapshots/1.jpg")
    b = mem.add(timestamp=ago(minutes=5), image_path="snapshots/2.jpg")
    mem.add(timestamp=ago(minutes=1), verified=0)        # rejected: ignored
    inv = tm.get_items_not_seen_since(60, now=NOW, belongings_dir=mem.dir)
    (m,) = [i for i in inv["not_seen_since"] if i["belonging_id"] == "mouse1"]
    assert m["sighting_id"] == b > a and m["seconds_since"] == 300


# ======================================================== read-only + persistence
def test_lookups_do_not_create_sightings_or_reset_clock(mem):
    mem.add(timestamp=ago(minutes=40), event="left_view")
    before = last(mem)
    tools = tools_for(mem)
    tools.tool_get_last_seen("mouse")
    tools.tool_get_last_seen("mouse", at_time=ago(minutes=50))
    tools.tool_search_sightings(belonging="mouse")
    tools.tool_list_belongings(not_seen_for_minutes=10)
    with dbmod.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] == 1
    assert last(mem) == before


def test_last_seen_survives_restart(tmp_path, belongings_dir):
    path = tmp_path / "persist.db"
    dbmod.init_db(path)
    register(belongings_dir, "My Mouse", "mouse", [], item_id="mouse1")
    with dbmod.connect(path) as db:
        dbmod.insert_sighting(db, object_name="mouse", timestamp=ago(minutes=7),
                              image_path="snapshots/x.jpg", belonging_id="mouse1",
                              belonging_name="My Mouse")
    dbmod.init_db(path)   # restart: schema upgrade is a no-op, rows remain
    r = tm.get_last_seen("mouse1", db_path=path, now=NOW, belongings_dir=belongings_dir)
    assert r["relative_time"] == "7 minutes ago"


# ======================================================== agent tools
def test_get_last_seen_tool_grounding(mem):
    mem.add(timestamp=ago(minutes=23), description="On the grey table", event="left_view")
    r = json.loads(tools_for(mem).tool_get_last_seen("my mouse"))
    assert r["relative_time"] == "23 minutes ago" and r["seconds_since"] == 1380
    assert r["last_seen_at"] == ago(minutes=23) and r["status"] == "left_view"
    assert r["location_description"] == "On the grey table"
    assert r["verified"] == "unchecked" and "image_url" not in r


def test_tool_unknown_and_never_seen(mem):
    tools = tools_for(mem)
    keys = json.loads(tools.run("get_last_seen", {"belonging": "keys"}))
    assert keys["message"] == "I haven't recorded a sighting of your Keys yet."
    assert "location_description" not in keys          # nothing invented
    assert tools.run("get_last_seen", {"belonging": "umbrella"}).startswith("No registered")
    assert tools.run("get_last_seen", {"belonging": "mouse", "at_time": "noonish"}) \
        .startswith("Tool error")


def test_tool_keeps_zero_values(mem):
    mem.add(timestamp=NOW.isoformat(), match_score=0.0, verified=1)
    r = json.loads(tools_for(mem).tool_get_last_seen("my mouse"))
    assert r["seconds_since"] == 0 and r["match_score"] == 0.0 and r["verified"] is True


def test_tool_historical_and_around(mem, day):
    tools = tools_for(mem)
    r = json.loads(tools.run("get_last_seen", {"belonging": "mouse", "at_time": day["at"](10, 0)}))
    assert r["sighting_id"] == day["desk"] and r["seconds_before_requested"] == 480
    assert r["later_observation"]["sighting_id"] == day["laptop"]
    assert tools.lookups[0]["requested_at"] == day["at"](10, 0)
    near = json.loads(tools.run("get_last_seen", {
        "belonging": "mouse", "at_time": day["at"](10, 5), "around_minutes": 15}))
    assert near["sighting_id"] == day["laptop"] and near["offset_seconds"] == 180
    none = json.loads(tools.run("get_last_seen", {
        "belonging": "mouse", "at_time": day["at"](15, 0), "around_minutes": 5}))
    assert none["message"].startswith("No sighting within 5 minutes")


def test_tool_ambiguous_names(mem, belongings_dir):
    register(belongings_dir, "My Black Mouse", "mouse", [], item_id="mouse2")
    mem.add(timestamp=ago(minutes=3))
    out = json.loads(tools_for(mem).tool_get_last_seen("mouse"))
    assert out["ambiguous"] is True
    assert {o["belonging_name"] for o in out["matches"]} == {"My Mouse", "My Black Mouse"}
    # an exact name still picks exactly one
    assert "ambiguous" not in json.loads(tools_for(mem).tool_get_last_seen("My Black Mouse"))


def test_list_belongings_inventory_mode(mem):
    mem.add(timestamp=ago(minutes=5))
    mem.add("bag1", timestamp=ago(hours=2))
    tools = tools_for(mem)
    inv = json.loads(tools.run("list_belongings", {"not_seen_for_minutes": 60}))
    assert [i["belonging_name"] for i in inv["not_seen_since"]] == ["My Bag"]
    assert inv["not_seen_since"][0]["relative_time"] == "2 hours ago"
    assert {i["belonging_name"] for i in inv["never_seen"]} == {"My Keys", "My AirPods"}
    # default mode unchanged
    assert isinstance(json.loads(tools.run("list_belongings", {})), list)


def test_search_sightings_absolute_window(mem):
    mem.add(timestamp=ago(minutes=10))
    mem.add(timestamp=ago(minutes=40))
    tools = tools_for(mem)
    # 10:30-10:55 PDT = 17:30-17:55 UTC
    out = json.loads(tools.run("search_sightings", {
        "start_time": "2026-10-24T10:30:00-07:00", "end_time": "2026-10-24T10:55:00-07:00"}))
    assert len(out) == 1 and "10 minutes ago" in out[0]["when"]
    assert tools.run("search_sightings", {"start_time": "noonish"}).startswith("Tool error")


def test_agent_answers_from_tool_values_with_citations_and_lookups(mem, day):
    """Grok is routed to get_last_seen(at_time) and sees Python's numbers;
    the API returns the cited photo and the requested-vs-observed lookup."""
    def final(kw):
        assert kw["input"][0]["type"] == "function_call_output"
        result = json.loads(kw["input"][0]["output"])
        assert result["seconds_before_requested"] == 480
        assert result["absolute_time"] == "today at 9:52 AM"   # in the user's zone (UTC)
        return reply(message(f"The last sighting before 10 AM was at 9:52 AM, on the desk "
                             f"[#{result['sighting_id']}]."))
    client = FakeClient(
        reply(fcall("get_last_seen", {"belonging": "mouse", "at_time": day["at"](10, 0)})),
        final)
    agent = agentmod.RecallAgent(client=client, tools=tools_for(mem))
    out = agent.ask("where was my mouse at 10?", user_tz="UTC")
    assert out["sightings"][0]["id"] == day["desk"]
    assert out["lookups"][0]["requested_at"] == day["at"](10, 0)
    assert agent.tools.lookups == [] and agent.tools.tz is None  # shared tools untouched


def test_agent_uses_browser_timezone(mem):
    tz_or_skip("America/Los_Angeles")
    mem.add(timestamp=ago(minutes=5))
    t = tools_for(mem).for_request("America/Los_Angeles")
    r = json.loads(t.tool_get_last_seen("my mouse"))
    assert r["absolute_time"] == "today at 10:55 AM"
    assert json.loads(t.tool_get_context())["now_iso"].endswith("-07:00")
    bad = tools_for(mem).for_request("Mars/Olympus")
    assert json.loads(bad.tool_get_last_seen("my mouse"))["absolute_time"] == "today at 5:55 PM"


def test_system_prompt_rules():
    s = agentmod.SYSTEM
    assert "never work out times or durations yourself" in s
    assert "never use a later sighting" in s
    assert 'never "it is on the\n  table"' in s or "never \"it is on the" in s
    assert "haven't recorded a sighting" in s


# ======================================================== API
@pytest.fixture
def api(mem, monkeypatch):
    import search
    import server
    monkeypatch.setattr(search, "BELONGINGS_DIR", mem.dir)
    return TestClient(server.app)


def test_last_seen_endpoint(api, mem, day):
    r = api.get("/api/belongings/mouse1/last-seen").json()
    assert r["sighting_id"] == day["left"] and r["status"] == "left_view"
    assert len(r["previous"]) == 2 and r["now"] == NOW.isoformat()
    h = api.get("/api/belongings/mouse1/last-seen", params={"at": day["at"](10, 0)}).json()
    assert h["sighting_id"] == day["desk"] and h["seconds_before_requested"] == 480
    assert api.get("/api/belongings/mouse1/last-seen", params={"at": "soon"}).status_code == 422
    assert api.get("/api/belongings/keys1/last-seen").json()["status"] == "never_seen"
    assert api.get("/api/belongings/nope/last-seen").status_code == 404


def test_existing_endpoints_keep_their_shape(api, mem):
    mem.add(timestamp=ago(minutes=30))
    sid = mem.add(timestamp=ago(minutes=10))
    items = {i["id"]: i for i in api.get("/api/belongings").json()}
    m = items["mouse1"]
    assert {"id", "name", "category", "photos", "sightings", "last"} <= set(m)
    assert m["last"]["id"] == sid and m["last_seen"]["sighting_id"] == sid
    assert m["last_seen"]["relative_time"] == "10 minutes ago"
    assert items["keys1"]["last"] is None
    rows = api.get("/api/sightings", params={"belonging_id": "mouse1"}).json()
    assert [r["id"] for r in rows][0] == sid
    assert api.get("/api/sightings/latest", params={"q": "my mouse"}).json()["id"] == sid


def test_ask_accepts_optional_timezone(api, mem, monkeypatch):
    import server
    seen = {}
    monkeypatch.setattr(server.agent, "ask",
                        lambda q, h, tz=None: seen.update(tz=tz) or
                        {"answer": "ok", "sightings": [], "steps": []})
    assert api.post("/api/ask", json={"question": "hi"}).status_code == 200
    assert seen["tz"] is None
    api.post("/api/ask", json={"question": "hi", "timezone": "Europe/London"})
    assert seen["tz"] == "Europe/London"


# ======================================================== watcher (reused)
def test_watcher_neutral_alert_once_per_disappearance(mem):
    mem.add(timestamp=ago(minutes=41), description="On the grey table", event="left_view")
    new = watcher.run_once(client=None, now=NOW, belongings_dir=mem.dir)
    assert [n["message"] for n in new] == [
        "Your Mouse hasn't been seen for 41 minutes. Last seen: On the grey table."]
    assert watcher.run_once(client=None, now=NOW + timedelta(minutes=30),
                            belongings_dir=mem.dir) == []     # same disappearance
    mem.add(timestamp=(NOW + timedelta(minutes=5)).isoformat(), event="left_view")
    later = NOW + timedelta(minutes=20)
    assert len(watcher.run_once(client=None, now=later, belongings_dir=mem.dir)) == 1


def test_watcher_detail_uses_shared_duration(mem):
    mem.add(timestamp=ago(hours=1, minutes=5), event="left_view")
    (c,) = watcher.find_candidates(now=NOW, belongings_dir=mem.dir)
    assert c["detail"] == "out of view for 1 hour 5 minutes"
