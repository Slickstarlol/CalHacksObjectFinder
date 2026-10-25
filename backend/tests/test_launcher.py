"""launcher/: sequential start, readiness, failures, stopping, control panel API.

Uses real child processes (tiny Python one-liners / a real HTTP server), no mocks
of subprocess, so process-tree killing and readiness polling are exercised.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / "launcher"
sys.path.insert(0, str(LAUNCHER))
import core  # noqa: E402
import launcher as launcher_mod  # noqa: E402

PY = sys.executable


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pid_alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # zombie? (reaped by nobody yet) counts as dead
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except OSError:
        return True


def step(id, command, ready=None, **kw):
    return core.Step(id=id, name=id.title(), description="", command=command,
                     enabled=kw.pop("enabled", True), ready=ready or {"type": "exit"},
                     timeout=kw.pop("timeout", 15), **kw)


def http_server_cmd(port, marker=None):
    code = (f"import http.server,socketserver,time;"
            + (f"open({str(marker)!r},'a').write('http %f\\n' % time.time());" if marker else "")
            + f"http.server.ThreadingHTTPServer(('127.0.0.1',{port}),http.server.SimpleHTTPRequestHandler).serve_forever()")
    return [PY, "-c", code]


def marker_cmd(marker, label, code=0, sleep=0):
    return [PY, "-c", f"import time,sys;time.sleep({sleep});open({str(marker)!r},'a').write('{label} %f\\n' % time.time());sys.exit({code})"]


def runner_for(steps, tmp_path, **kw):
    return core.Runner(steps, repo_root=tmp_path, log_dir=tmp_path / "logs",
                       tools={"python": PY, "npm": None, "spacetime": None}, poll=0.1, **kw)


# --------------------------------------------------------------- the real steps.json
def test_shipped_steps_file_is_valid(tmp_path):
    steps = core.load_steps(LAUNCHER / "steps.json", tmp_path / "none.json")
    ids = [s.id for s in steps]
    assert ids == ["check-grok", "spacetime", "publish", "server", "camera",
                   "dashboard", "live-dashboard"]
    for s in steps:
        assert s.ready["type"] in ("http", "port", "exit", "alive")
        assert (LAUNCHER.parent / s.cwd).is_dir(), f"{s.id}: cwd {s.cwd} missing"
        for tok in s.command:
            if tok.startswith("{"):
                assert tok in ("{python}", "{npm}", "{spacetime}")
                assert tok[1:-1] in s.needs
    # The server must come before the things that talk to it.
    assert ids.index("server") < ids.index("camera") < ids.index("dashboard")
    assert ids.index("spacetime") < ids.index("publish") < ids.index("live-dashboard")


def test_python_steps_point_at_real_modules():
    import importlib.util
    for s in core.load_steps(LAUNCHER / "steps.json", Path("/nonexistent")):
        if s.command[0] == "{python}" and s.command[1] == "-m":
            mod = s.command[2]
            if mod != "uvicorn":
                assert importlib.util.find_spec(mod), mod
            else:
                app_mod = s.command[3].split(":")[0]
                assert importlib.util.find_spec(app_mod), app_mod


def test_settings_remember_switches(tmp_path):
    settings = tmp_path / "settings.local.json"
    steps = core.load_steps(LAUNCHER / "steps.json", settings)
    by = {s.id: s for s in steps}
    by["camera"].enabled, by["spacetime"].enabled = False, True
    core.save_settings(steps, settings)
    again = {s.id: s for s in core.load_steps(LAUNCHER / "steps.json", settings)}
    assert again["camera"].enabled is False and again["spacetime"].enabled is True
    assert again["camera"].status == "off" and again["spacetime"].status == "waiting"


# --------------------------------------------------------------- sequencing
def test_steps_run_in_order_each_waits_for_previous(tmp_path):
    marker = tmp_path / "order.txt"
    port = free_port()
    steps = [
        step("one", marker_cmd(marker, "one", sleep=0.4)),
        step("web", http_server_cmd(port, marker), {"type": "http", "url": f"http://localhost:{port}/"}),
        step("two", marker_cmd(marker, "two")),
        step("worker", [PY, "-c", "import time; time.sleep(60)"], {"type": "alive", "seconds": 0.3}),
    ]
    r = runner_for(steps, tmp_path)
    try:
        assert r.run_sequence() is True
        assert [s.status for s in steps] == ["done", "ready", "done", "ready"]
        labels = [l.split()[0] for l in marker.read_text().splitlines()]
        assert labels == ["one", "http", "two"]
        assert (tmp_path / "logs" / "web.log").exists()
    finally:
        r.stop_all()
    assert all(not pid_alive(s.proc.pid) for s in steps)


def test_disabled_steps_are_skipped(tmp_path):
    marker = tmp_path / "m.txt"
    steps = [step("a", marker_cmd(marker, "a")), step("b", marker_cmd(marker, "b"), enabled=False),
             step("c", marker_cmd(marker, "c"))]
    r = runner_for(steps, tmp_path)
    assert r.run_sequence()
    assert [l.split()[0] for l in marker.read_text().splitlines()] == ["a", "c"]
    assert steps[1].proc is None


def test_failure_skips_the_rest_and_keeps_earlier_running(tmp_path):
    port = free_port()
    marker = tmp_path / "m.txt"
    steps = [
        step("web", http_server_cmd(port), {"type": "http", "url": f"http://localhost:{port}/"}),
        step("bad", [PY, "-c", "print('boom: missing camera'); raise SystemExit(3)"]),
        step("later", marker_cmd(marker, "later")),
    ]
    r = runner_for(steps, tmp_path)
    try:
        assert r.run_sequence() is False
        assert steps[0].status == "ready" and pid_alive(steps[0].proc.pid)
        assert steps[1].status == "failed" and "code 3" in steps[1].message
        assert steps[2].status == "skipped" and not marker.exists()
        assert any("boom: missing camera" in l for l in r.logs("bad")["lines"])
    finally:
        r.stop_all()


def test_long_running_step_that_crashes_while_starting_fails(tmp_path):
    steps = [step("srv", [PY, "-c", "raise SystemExit('port in use')"],
                  {"type": "http", "url": f"http://localhost:{free_port()}/"})]
    r = runner_for(steps, tmp_path)
    assert r.run_sequence() is False
    assert steps[0].status == "failed" and "unexpectedly" in steps[0].message


def test_timeout_kills_the_process(tmp_path):
    s = step("never", [PY, "-c", "import time; time.sleep(60)"],
             {"type": "http", "url": f"http://localhost:{free_port()}/"}, timeout=1)
    r = runner_for([s], tmp_path)
    assert r.run_sequence() is False
    assert s.status == "failed" and "not ready after 1s" in s.message
    assert not pid_alive(s.proc.pid)


def test_already_running_service_is_not_started_twice(tmp_path):
    port = free_port()
    outside = subprocess.Popen(http_server_cmd(port))
    try:
        deadline = time.time() + 10
        while not core.http_ok(f"http://localhost:{port}/") and time.time() < deadline:
            time.sleep(0.1)
        s = step("web", http_server_cmd(port), {"type": "http", "url": f"http://localhost:{port}/"})
        r = runner_for([s], tmp_path)
        assert r.run_sequence()
        assert s.status == "already-running" and s.proc is None
        r.stop_all()
        assert outside.poll() is None      # we never touch processes we didn't start
    finally:
        outside.kill()
        outside.wait()


def test_missing_tool_fails_with_install_hint(tmp_path):
    s = step("dash", ["{npm}", "run", "dev"], needs=["npm"])
    r = runner_for([s], tmp_path)
    assert r.run_sequence() is False
    assert "npm not found" in s.message and "nodejs.org" in s.message


@pytest.mark.skipif(os.name == "nt", reason="fake npm is a POSIX shell script")
def test_npm_install_runs_first_when_node_modules_missing(tmp_path):
    fake_npm = tmp_path / "npm"
    fake_npm.write_text("#!/bin/sh\necho \"npm $@\" >> calls.txt\n"
                        "[ \"$1\" = install ] && mkdir -p node_modules\nexit 0\n")
    fake_npm.chmod(0o755)
    app = tmp_path / "app"
    app.mkdir()
    s = step("dash", ["{npm}", "run", "build"], cwd="app", needs=["npm"],
             install_if_missing="node_modules")
    r = core.Runner([s], repo_root=tmp_path, log_dir=tmp_path / "logs",
                    tools={"python": PY, "npm": str(fake_npm), "spacetime": None}, poll=0.1)
    assert r.run_sequence()
    assert (app / "calls.txt").read_text().split("\n")[:2] == ["npm install", "npm run build"]
    (app / "calls.txt").unlink()
    s.status = "waiting"
    assert r.run_sequence()               # second run: no install
    assert (app / "calls.txt").read_text().strip() == "npm run build"


def test_stop_kills_whole_process_tree(tmp_path):
    pidfile = tmp_path / "child.pid"
    parent = (f"import subprocess,sys,time;"
              f"c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);"
              f"open({str(pidfile)!r},'w').write(str(c.pid));time.sleep(120)")
    s = step("tree", [PY, "-c", parent], {"type": "alive", "seconds": 0.5})
    r = runner_for([s], tmp_path)
    assert r.run_sequence()
    deadline = time.time() + 5
    while not pidfile.exists() and time.time() < deadline:
        time.sleep(0.05)
    child = int(pidfile.read_text())
    assert pid_alive(child)
    r.stop_all()
    time.sleep(0.5)
    assert not pid_alive(s.proc.pid) and not pid_alive(child)
    assert s.status == "stopped"


def test_watch_notices_a_step_that_died_later(tmp_path):
    s = step("short", [PY, "-c", "import time; time.sleep(0.8)"], {"type": "alive", "seconds": 0.2})
    r = runner_for([s], tmp_path)
    assert r.run_sequence() and s.status == "ready"
    time.sleep(1.2)
    st = {x["id"]: x for x in r.state()["steps"]}
    assert st["short"]["status"] == "exited"


def test_stop_all_during_startup_cancels_the_sequence(tmp_path):
    marker = tmp_path / "m.txt"
    steps = [step("slow", [PY, "-c", "import time; time.sleep(60)"],
                  {"type": "http", "url": f"http://localhost:{free_port()}/"}, timeout=60),
             step("after", marker_cmd(marker, "after"))]
    r = runner_for(steps, tmp_path)
    r.start_all()
    time.sleep(0.6)
    t0 = time.time()
    r.stop_all()
    assert time.time() - t0 < 10
    assert not marker.exists() and not pid_alive(steps[0].proc.pid)


def test_command_placeholders_and_pythonpath(tmp_path):
    out = tmp_path / "env.txt"
    (tmp_path / "backend").mkdir()
    s = step("env", ["{python}", "-c",
                     f"import os,sys;open({str(out)!r},'w').write(os.environ['PYTHONPATH']+'|'+sys.executable)"])
    r = runner_for([s], tmp_path)
    assert r.run_sequence()
    pythonpath, exe = out.read_text().split("|")
    assert pythonpath.split(os.pathsep)[0] == str(tmp_path / "backend")
    assert Path(exe).resolve() == Path(PY).resolve()


# --------------------------------------------------------------- control panel API
@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SETTINGS_FILE", tmp_path / "settings.local.json")
    marker = tmp_path / "m.txt"
    steps = [step("a", marker_cmd(marker, "a")), step("b", marker_cmd(marker, "b"), enabled=False)]
    r = runner_for(steps, tmp_path)
    quit_called = threading.Event()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                launcher_mod.make_handler(r, steps, quit_called.set))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"

    def call(path, body=None, origin=None):
        data = None if body is None else json.dumps(body).encode()
        req = Request(base + path, data=data, method="POST" if body is not None else "GET")
        if origin:
            req.add_header("Origin", origin)
        try:
            with urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read() or b"null") if "json" in resp.headers.get("Content-Type", "") else resp.read()
        except Exception as e:  # HTTPError
            return e.code, json.loads(e.read())
    yield type("P", (), {"call": call, "runner": r, "marker": marker, "base": base,
                         "quit": quit_called, "settings": tmp_path / "settings.local.json"})
    httpd.shutdown()
    r.stop_all()


def test_panel_page_and_state(panel):
    code, html = panel.call("/")
    assert code == 200 and b"RecallAI Launcher" in html and b"Start all" in html
    code, st = panel.call("/api/state")
    assert [s["id"] for s in st["steps"]] == ["a", "b"]
    assert st["steps"][1]["status"] == "off"


def test_panel_toggle_start_and_logs(panel):
    code, st = panel.call("/api/toggle", {"id": "b", "enabled": True})
    assert code == 200 and st["steps"][1]["enabled"] is True
    assert json.loads(panel.settings.read_text())["enabled"] == {"a": True, "b": True}
    assert panel.call("/api/start", {})[1] == {"started": True}
    deadline = time.time() + 10
    while time.time() < deadline:
        st = panel.call("/api/state")[1]
        if all(s["status"] == "done" for s in st["steps"]):
            break
        time.sleep(0.1)
    assert [s["status"] for s in st["steps"]] == ["done", "done"]
    logs = panel.call("/api/logs/a?since=0")[1]
    assert logs["next"] >= 2 and any("done" in l for l in logs["lines"])
    assert panel.call(f"/api/logs/a?since={logs['next']}")[1]["lines"] == []


def test_panel_rejects_other_websites(panel):
    code, body = panel.call("/api/start", {}, origin="https://evil.example")
    assert code == 403
    assert not panel.marker.exists()
    code, _ = panel.call("/api/toggle", {"id": "nope", "enabled": True})
    assert code == 404
    assert panel.call("/api/step/a/explode", {})[0] == 404
    assert panel.call("/api/step/a/b/c", {})[0] == 404


def test_panel_quit_stops_everything(panel):
    assert panel.call("/api/quit", {})[0] == 200
    assert panel.quit.wait(5)


def test_cli_list_and_only_validation(capsys):
    assert launcher_mod.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "server" in out and "live-dashboard" in out
    with pytest.raises(SystemExit):
        launcher_mod.main(["--only", "server,nonsense", "--list"])


# --------------------------------------------------------------- dropdown options
def test_camera_step_has_an_output_dropdown(tmp_path):
    settings = tmp_path / "settings.local.json"
    by = {s.id: s for s in core.load_steps(LAUNCHER / "steps.json", settings)}
    cam = by["camera"]
    assert [o["id"] for o in cam.options] == ["camera"] and cam.values == {"camera": "0"}
    assert cam.public()["options"] == [
        {"id": "camera", "label": "Output", "choices": "cameras", "value": "0"}]
    r = runner_for(list(by.values()), tmp_path)
    assert r._expand(cam)[-2:] == ["--camera", "0"]

    cam.values["camera"] = "2"
    core.save_settings(list(by.values()), settings)
    again = {s.id: s for s in core.load_steps(LAUNCHER / "steps.json", settings)}
    assert again["camera"].values == {"camera": "2"}
    assert json.loads(settings.read_text())["options"] == {"camera": {"camera": "2"}}


def fake_cameras(monkeypatch, tmp_path, found):
    """The camera list command, faked: prints `found` and logs its argv."""
    argv_log = tmp_path / "argv.json"
    code = (f"import json,sys;open({str(argv_log)!r},'w').write(json.dumps(sys.argv[1:]));"
            f"print('noise');print(json.dumps({found!r}))")
    monkeypatch.setitem(core.CHOICE_COMMANDS, "cameras", [PY, "-c", code])
    return argv_log


def cam_step(marker, **kw):
    code = (f"import sys,time;open({str(marker)!r},'a').write(' '.join(sys.argv[1:])+chr(10));"
            "time.sleep(30)")
    s = step("camera", [PY, "-c", code], ready={"type": "alive", "seconds": 0.2}, **kw)
    s.options = [{"id": "camera", "label": "Output", "flag": "--camera", "choices": "cameras"}]
    s.values = {"camera": "0"}
    return s


def test_choices_list_cameras_and_skip_the_one_in_use(tmp_path, monkeypatch):
    argv_log = fake_cameras(monkeypatch, tmp_path, [
        {"index": 0, "width": 1280, "height": 720}, {"index": 2, "width": 640, "height": 480}])
    s = cam_step(tmp_path / "m.txt")
    r = runner_for([s], tmp_path)
    got = r.choices("cameras")
    assert got == {"error": None, "choices": [
        {"value": "0", "label": "Camera 0 · 1280×720"},
        {"value": "2", "label": "Camera 2 · 640×480"}]}
    assert json.loads(argv_log.read_text()) == []

    assert r.start_step(s)            # camera 0 is now busy: don't open it again
    try:
        fake_cameras(monkeypatch, tmp_path, [{"index": 2, "width": 640, "height": 480}])
        got = r.choices("cameras")
        assert json.loads(argv_log.read_text()) == ["--skip", "0"]
        assert [c["label"] for c in got["choices"]] == ["Camera 0 (in use)", "Camera 2 · 640×480"]
    finally:
        r.stop_step(s)

    monkeypatch.setitem(core.CHOICE_COMMANDS, "cameras", [PY, "-c", "import sys;sys.exit(3)"])
    assert r.choices("cameras")["error"].startswith("Couldn't look for cameras")


def test_changing_the_camera_restarts_a_running_step(tmp_path):
    marker = tmp_path / "m.txt"
    s = cam_step(marker)
    r = runner_for([s], tmp_path)
    _, restarting = r.set_option("camera", "camera", "1")    # not running: just remembered
    assert restarting is False and s.values["camera"] == "1"
    assert r.start_step(s)
    try:
        first_pid = s.pid
        _, restarting = r.set_option("camera", "camera", "2")
        assert restarting is True
        deadline = time.time() + 15
        while time.time() < deadline and not (s.pid != first_pid and s.status == "ready"):
            time.sleep(0.1)
        assert s.pid != first_pid and s.status == "ready"
        assert marker.read_text().split("\n")[:2] == ["--camera 1", "--camera 2"]
        assert not pid_alive(first_pid)
    finally:
        r.stop_step(s)
    with pytest.raises(KeyError):
        r.set_option("camera", "nope", "1")


def test_panel_dropdown_api(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SETTINGS_FILE", tmp_path / "settings.local.json")
    fake_cameras(monkeypatch, tmp_path, [{"index": 1, "width": 640, "height": 480}])
    steps = [cam_step(tmp_path / "m.txt")]
    r = runner_for(steps, tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), launcher_mod.make_handler(r, steps, lambda: None))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"

    def call(path, body=None):
        req = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                      method="GET" if body is None else "POST")
        try:
            with urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except Exception as e:
            return e.code, json.loads(e.read())
    try:
        assert call("/api/choices/cameras") == (200, {"error": None, "choices": [
            {"value": "1", "label": "Camera 1 · 640×480"}]})
        assert call("/api/choices/nope")[0] == 404
        code, st = call("/api/option", {"id": "camera", "option": "camera", "value": "1"})
        assert code == 200 and st["restarting"] is False
        assert st["steps"][0]["options"][0]["value"] == "1"
        saved = json.loads((tmp_path / "settings.local.json").read_text())
        assert saved["options"] == {"camera": {"camera": "1"}}
        assert call("/api/option", {"id": "camera", "option": "x", "value": "1"})[0] == 404
        assert call("/api/option", {"id": "nope", "option": "camera", "value": "1"})[0] == 404
    finally:
        httpd.shutdown()
        r.stop_all()
