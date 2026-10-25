"""Grok (xAI Responses API) features with a scripted fake client.

No API key needed. The fake records every request, so these tests check
both what we SEND to Grok (images, tools, forced tool calls, call_id
chaining) and how we HANDLE its replies.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from recallai.ai import agent as agentmod
from recallai.memory import db as dbmod
from recallai.ai import watcher
from conftest import RED, make_object_image, register
from recallai.ai.interpreter import Interpreter
from recallai.ai.llm import VISION_MODEL
from recallai.vision.recognition import BelongingMatcher, HistogramEmbedder
from recallai.vision.recorder import Detection, SightingRecorder

FIX = Path(__file__).parent / "fixtures"
FRAME = str(FIX / "1792851263393_bottle.jpg")
NOW = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- fakes
def fcall(name, args, call_id="call_1"):
    return SimpleNamespace(type="function_call", name=name,
                           arguments=json.dumps(args), call_id=call_id)


def message(text):
    return SimpleNamespace(type="message", role="assistant",
                           content=[SimpleNamespace(type="output_text", text=text)])


def reply(*items):
    return SimpleNamespace(output=list(items))


class FakeClient:
    """Mimics client.responses.create(); `script` items are replies,
    exceptions to raise, or callables(kwargs) -> reply."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []
        self.responses = self

    def create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=str)))
        nxt = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(nxt, Exception):
            raise nxt
        r = nxt(kw) if callable(nxt) else nxt
        r.id = f"resp_{len(self.calls)}"
        return r


# ---------------------------------------------------------------- data
@pytest.fixture
def world(tmp_path, belongings_dir, monkeypatch):
    """A db with My Mouse (seen, then left_view 15 min ago) and My Bag."""
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "m.db")
    dbmod.init_db()
    register(belongings_dir, "My Mouse", "mouse",
             [make_object_image(RED, seed=i) for i in range(3)], item_id="mouse1")
    register(belongings_dir, "My Black Backpack", "backpack",
             [make_object_image(RED, seed=9)], item_id="bag1")

    def add(**kw):
        base = dict(object_name="mouse", timestamp=NOW.isoformat(),
                    image_path=FRAME, belonging_id="mouse1",
                    belonging_name="My Mouse", event="seen", bbox="64,299,99,370",
                    crop_path=FRAME, match_score=0.9)
        base.update(kw)
        with dbmod.connect() as db:
            return dbmod.insert_sighting(db, **base)

    ids = {
        "old": add(timestamp=(NOW - timedelta(hours=3)).isoformat(),
                   description="In the backpack's front pocket", place="backpack"),
        "seen": add(timestamp=(NOW - timedelta(minutes=20)).isoformat(),
                    description="On the gray table next to a laptop", place="hackathon table",
                    nearby="laptop, bottle"),
        "left": add(timestamp=(NOW - timedelta(minutes=15)).isoformat(),
                    event="left_view", description="On the gray table, right of the laptop",
                    place="hackathon table", latitude=37.8028, longitude=-122.4490,
                    accuracy=10.0),
    }
    return SimpleNamespace(dir=belongings_dir, ids=ids, add=add, tmp=tmp_path)


def row(sid):
    with dbmod.connect() as db:
        return db.execute("SELECT * FROM sightings WHERE id=?", (sid,)).fetchone()


# ================================================================ interpreter
def interp_reply(same="yes", loc="On the gray table, left of the green bag",
                 place="hackathon table"):
    return reply(fcall("record_sighting", {
        "same_item": same, "verify_reason": "same scuff on the left side",
        "location": loc, "place": place}))


def test_interpreter_sends_refs_and_boxed_frame_and_saves_result(world):
    client = FakeClient(interp_reply())
    it = Interpreter(client=client, belongings_dir=world.dir, owner="Kent")
    it.process(world.ids["seen"])

    req = client.calls[0]
    assert req["model"] == VISION_MODEL
    assert req["tool_choice"] == "required"
    assert [t["name"] for t in req["tools"]] == ["record_sighting"]
    assert req["tools"][0]["type"] == "function"
    content = req["input"][0]["content"]
    images = [c for c in content if c["type"] == "input_image"]
    assert len(images) == 3  # 2 reference photos + the boxed frame
    assert images[0]["image_url"].startswith("data:image/jpeg;base64,")
    assert "Kent" in content[-1]["text"] and "My Mouse" in content[-1]["text"]

    r = row(world.ids["seen"])
    assert r["verified"] == 1 and r["place"] == "hackathon table"
    assert r["description"].startswith("On the gray table")


def test_interpreter_errors_if_grok_answers_without_the_tool(world):
    client = FakeClient(reply(message("I think it's a mouse.")))
    with pytest.raises(RuntimeError, match="record_sighting"):
        Interpreter(client=client, belongings_dir=world.dir).process(world.ids["seen"])


def test_rejected_sighting_is_hidden_everywhere(world, monkeypatch):
    from fastapi.testclient import TestClient
    from recallai.api import search
    from recallai.api import server
    monkeypatch.setattr(search, "BELONGINGS_DIR", world.dir)
    client = FakeClient(interp_reply(same="no"))
    Interpreter(client=client, belongings_dir=world.dir).process(world.ids["left"])
    assert row(world.ids["left"])["verified"] == 0

    api = TestClient(server.app)
    ids = [s["id"] for s in api.get("/api/sightings", params={"q": "my mouse"}).json()]
    assert world.ids["left"] not in ids and world.ids["seen"] in ids

    tools = agentmod.MemoryTools(belongings_dir=world.dir, now=NOW)
    out = json.loads(tools.tool_search_sightings(belonging="mouse"))
    assert world.ids["left"] not in [s["id"] for s in out]

    assert watcher.find_candidates(gps=None, now=NOW, belongings_dir=world.dir) == []


def test_interpreter_worker_survives_api_errors(world):
    client = FakeClient(RuntimeError("rate limited"), interp_reply())
    it = Interpreter(client=client, belongings_dir=world.dir)
    it.submit(world.ids["seen"])   # fails
    it.submit(world.ids["left"])   # still processed
    it.wait()
    assert row(world.ids["seen"])["description"] == "On the gray table next to a laptop"
    assert row(world.ids["left"])["verified"] == 1


def test_interpreter_off_without_key(monkeypatch, world):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    it = Interpreter(belongings_dir=world.dir)
    assert not it.enabled
    it.submit(world.ids["seen"])  # no-op, no crash


def test_recorder_hands_each_saved_sighting_to_grok(tmp_path, belongings_dir):
    register(belongings_dir, "My Bag", "backpack",
             [make_object_image(RED, seed=i) for i in range(3)])
    saved = []
    rec = SightingRecorder(BelongingMatcher(HistogramEmbedder(), belongings_dir),
                           db_path=tmp_path / "x.db", snapshot_dir=tmp_path / "s",
                           on_saved=saved.append)
    import numpy as np
    frame = np.full((480, 640, 3), 90, np.uint8)
    frame[150:270, 40:200] = make_object_image(RED, seed=50)
    rec.process(frame, [Detection("backpack", 0.9, (40, 150, 200, 270))], now=0)
    rec.close()
    assert saved == [1]


# ================================================================ agent
def test_agent_runs_function_calls_then_answers_with_citations(world):
    left = world.ids["left"]
    agent_script = [
        reply(fcall("search_sightings", {"belonging": "my mouse"}, "a")),
        reply(fcall("look_at_photo", {"sighting_id": left,
                                      "question": "What is next to it?"}, "b")),
        reply(message(f"Check the gray hackathon table, just right of your laptop "
                      f"[#{left}]. It was put down there 15 minutes ago.")),
    ]

    def dispatch(kw):
        if "tools" not in kw:  # the separate vision request from look_at_photo
            return reply(message("A black laptop is directly to its left."))
        return agent_script.pop(0)

    client = FakeClient(dispatch)
    tools = agentmod.MemoryTools(belongings_dir=world.dir, now=NOW)
    out = agentmod.RecallAgent(client=client, tools=tools).ask("where's my mouse??")

    assert "gray hackathon table" in out["answer"]
    assert [s["id"] for s in out["sightings"]] == [left]
    assert [s["tool"] for s in out["steps"]] == ["search_sightings", "look_at_photo"]

    first, second, vision, third = client.calls
    assert first["input"][0] == {"role": "system", "content": agentmod.SYSTEM}
    assert {t["name"] for t in first["tools"]} == {
        "list_belongings", "search_sightings", "get_last_seen", "look_at_photo",
        "get_context", "list_people"}
    # results go back as function_call_output, chained to the previous response
    out1 = second["input"][0]
    assert out1["type"] == "function_call_output" and out1["call_id"] == "a"
    assert "right of the laptop" in out1["output"]
    assert second["previous_response_id"] == "resp_1"
    # look_at_photo = a separate vision request with the boxed frame
    assert [c["type"] for c in vision["input"][0]["content"]] == [
        "input_image", "input_image", "input_text"]
    assert third["input"][0]["call_id"] == "b"
    assert "black laptop is directly to its left" in third["input"][0]["output"]
    assert third["previous_response_id"] == "resp_2"


def test_agent_handles_parallel_calls(world):
    client = FakeClient(
        reply(fcall("get_context", {}, "x"), fcall("list_belongings", {}, "y")),
        reply(message("Done.")))
    out = agentmod.RecallAgent(client=client, tools=agentmod.MemoryTools(
        belongings_dir=world.dir)).ask("status?")
    assert out["answer"] == "Done."
    assert [o["call_id"] for o in client.calls[1]["input"]] == ["x", "y"]


def test_agent_passes_conversation_history(world):
    client = FakeClient(reply(message("Yes.")))
    agentmod.RecallAgent(client=client, tools=agentmod.MemoryTools(
        belongings_dir=world.dir)).ask("and before that?", history=[
            {"role": "user", "content": "where's my mouse"},
            {"role": "assistant", "content": "On the table [#3]."}])
    roles = [m["role"] for m in client.calls[0]["input"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_agent_stops_after_max_turns(world):
    client = FakeClient(reply(fcall("get_context", {})))
    out = agentmod.RecallAgent(client=client, tools=agentmod.MemoryTools(
        belongings_dir=world.dir)).ask("loop forever")
    assert len(client.calls) == agentmod.MAX_TURNS
    assert "couldn't finish" in out["answer"]


def test_agent_survives_bad_tool_arguments(world):
    client = FakeClient(
        reply(SimpleNamespace(type="function_call", name="search_sightings",
                              arguments="{not json", call_id="z")),
        reply(message("ok")))
    out = agentmod.RecallAgent(client=client, tools=agentmod.MemoryTools(
        belongings_dir=world.dir)).ask("x")
    assert out["answer"] == "ok"


def test_agent_without_key_explains(monkeypatch, world):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    out = agentmod.RecallAgent(tools=agentmod.MemoryTools(belongings_dir=world.dir)).ask("hi")
    assert "XAI_API_KEY" in out["answer"]


# ----- the tools themselves (what Grok sees)
@pytest.fixture
def tools(world):
    gps = {"latitude": 37.8100, "longitude": -122.4490, "accuracy": 8}
    return agentmod.MemoryTools(belongings_dir=world.dir, now=NOW,
                                gps_provider=lambda: gps)


def test_tool_list_belongings(tools, world):
    items = {i["name"]: i for i in json.loads(tools.tool_list_belongings())}
    mouse = items["My Mouse"]["last_sighting"]
    assert mouse["event"] == "left_view" and mouse["id"] == world.ids["left"]
    assert "15 minutes ago" in mouse["when"]
    assert 750 < mouse["distance_from_phone_now_m"] < 900
    assert items["My Black Backpack"]["last_sighting"] == "never seen"


@pytest.mark.parametrize("name,expect", [
    ("My Mouse", "mouse1"), ("mouse", "mouse1"), ("my bag", None),
    ("backpack", "bag1"), ("the black backpack", "bag1"), ("umbrella", None)])
def test_tool_resolves_loose_names(tools, name, expect):
    with dbmod.connect() as db:
        ids = tools._resolve(db, name)
    assert ids == ([expect] if expect else [])


def test_tool_search_filters(tools, world):
    recent = json.loads(tools.tool_search_sightings(belonging="mouse", since_minutes=30))
    assert [s["id"] for s in recent] == [world.ids["left"], world.ids["seen"]]
    older = json.loads(tools.tool_search_sightings(belonging="mouse", before_minutes=60))
    assert [s["id"] for s in older] == [world.ids["old"]]
    pocket = json.loads(tools.tool_search_sightings(text="pocket"))
    assert [s["place"] for s in pocket] == ["backpack"]
    assert "list_belongings" in tools.tool_search_sightings(belonging="umbrella")
    assert tools.run("nope", {}) == "Unknown tool nope"
    assert tools.run("look_at_photo", {"sighting_id": 999, "question": "?"}) == "No sighting #999"
    assert "unavailable" in tools.run("look_at_photo",
                                      {"sighting_id": world.ids["left"], "question": "?"})


# ================================================================ watcher
def test_out_of_sight_rule(world):
    c = watcher.find_candidates(now=NOW, belongings_dir=world.dir)
    assert [(x["belonging"].name, x["reason"]) for x in c] == [("My Mouse", "out_of_sight")]
    soon = NOW - timedelta(minutes=10)  # only 5 minutes after it left
    assert watcher.find_candidates(now=soon, belongings_dir=world.dir) == []
    world.add(timestamp=NOW.isoformat())  # seen again afterwards -> fine
    assert watcher.find_candidates(now=NOW, belongings_dir=world.dir) == []


def test_moved_away_rule_beats_time(world):
    near = {"latitude": 37.80281, "longitude": -122.44901, "accuracy": 10}
    far = {"latitude": 37.8100, "longitude": -122.4490, "accuracy": 10}
    early = NOW - timedelta(minutes=12)  # only 3 min out of view
    assert watcher.find_candidates(gps=near, now=early, belongings_dir=world.dir) == []
    (c,) = watcher.find_candidates(gps=far, now=early, belongings_dir=world.dir)
    assert c["reason"] == "moved_away" and "m away" in c["detail"]


def test_run_once_without_grok_uses_template_and_dedups(world):
    new = watcher.run_once(client=None, now=NOW, belongings_dir=world.dir)
    assert len(new) == 1
    assert new[0]["message"].startswith("Your Mouse hasn't been seen for 15 minutes. Last seen: ")
    assert "right of the laptop" in new[0]["message"]
    assert watcher.run_once(client=None, now=NOW, belongings_dir=world.dir) == []


def test_grok_decides_and_writes_the_message(world):
    client = FakeClient(reply(fcall("decide_alerts", {"alerts": [{
        "belonging_id": "mouse1", "send": True, "urgency": "low",
        "message": "Your mouse has been on the hackathon table for 15 min."}]})))
    new = watcher.run_once(client=client, now=NOW, belongings_dir=world.dir)
    assert new[0]["message"].startswith("Your mouse")
    req = client.calls[0]
    assert req["tool_choice"] == "required"
    prompt = req["input"][0]["content"][0]["text"]
    assert "out_of_sight" in prompt and "right of the laptop" in prompt


def test_grok_can_veto_and_is_not_asked_again(world):
    client = FakeClient(reply(fcall("decide_alerts", {"alerts": [{
        "belonging_id": "mouse1", "send": False, "urgency": "low",
        "message": "Put away on purpose."}]})))
    assert watcher.run_once(client=client, now=NOW, belongings_dir=world.dir) == []
    assert watcher.run_once(client=client, now=NOW, belongings_dir=world.dir) == []
    assert len(client.calls) == 1


def test_grok_error_falls_back_to_template(world):
    client = FakeClient(RuntimeError("network down"))
    new = watcher.run_once(client=client, now=NOW, belongings_dir=world.dir)
    assert len(new) == 1


# ================================================================ server API
def test_ask_and_alert_endpoints(world, monkeypatch):
    from fastapi.testclient import TestClient
    from recallai.api import server
    monkeypatch.setattr(server.agent, "client", FakeClient(reply(message(
        f"On the gray table [#{world.ids['left']}]."))))
    monkeypatch.setattr(server.agent.tools, "belongings_dir", world.dir)
    api = TestClient(server.app)

    r = api.post("/api/ask", json={"question": "where is my mouse"})
    assert r.status_code == 200
    assert r.json()["sightings"][0]["id"] == world.ids["left"]
    assert api.post("/api/ask", json={"question": ""}).status_code == 422

    watcher.run_once(client=None, now=NOW, belongings_dir=world.dir)
    alerts = api.get("/api/alerts").json()
    assert len(alerts) == 1 and alerts[0]["crop_path"] == FRAME
    assert api.post(f"/api/alerts/{alerts[0]['id']}/dismiss").status_code == 200
    assert api.get("/api/alerts").json() == []
    assert api.post("/api/alerts/999/dismiss").status_code == 404
