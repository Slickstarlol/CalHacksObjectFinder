"""RecallAI launcher: start the whole project with one double-click.

    RecallAI.bat              (Windows: double-click)  -> opens the control panel
    python launcher/launcher.py                         -> same
    python launcher/launcher.py --run                   -> no browser panel: run the
                                                           enabled steps, Ctrl+C stops all
    python launcher/launcher.py --run --only server,camera
    python launcher/launcher.py --list

The control panel (http://127.0.0.1:8765) has a switch per step, Start all /
Stop all, per-step start/stop and live logs. Closing the panel tab does not stop
anything; press "Quit launcher" (or Ctrl+C in the launcher window) to stop all.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if getattr(sys, "frozen", False):   # PyInstaller: bundled files live in _MEIPASS
    sys.path.insert(0, getattr(sys, "_MEIPASS", ""))
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import core  # noqa: E402

PANEL_PORT = 8765


def _panel_html():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return (base / "panel.html").read_bytes()


def make_handler(runner, steps, on_quit):
    panel = _panel_html()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):   # keep the console quiet
            pass

        def _json(self, obj, status=200):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _local_only(self):
            # Only this computer may control the launcher.
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._json({"error": "forbidden"}, 403)
                return False
            return True

        def do_GET(self):
            if not self._local_only():
                return
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(panel)))
                self.end_headers()
                self.wfile.write(panel)
            elif path == "/api/state":
                self._json(runner.state())
            elif path.startswith("/api/logs/"):
                sid = path.rsplit("/", 1)[-1]
                since = 0
                if "since=" in self.path:
                    try:
                        since = int(self.path.split("since=")[1].split("&")[0])
                    except ValueError:
                        pass
                if sid not in runner.by_id:
                    return self._json({"error": "unknown step"}, 404)
                self._json(runner.logs(sid, since))
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if not self._local_only():
                return
            # Block cross-site requests from other web pages (CSRF).
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{self.server.server_port}",
                                         f"http://localhost:{self.server.server_port}"):
                return self._json({"error": "forbidden origin"}, 403)
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
            path = self.path.split("?")[0]
            if path == "/api/start":
                ok = runner.start_all()
                return self._json({"started": ok})
            if path == "/api/stop":
                threading.Thread(target=runner.stop_all, daemon=True).start()
                return self._json({"stopping": True})
            if path == "/api/toggle":
                sid = body.get("id")
                if sid not in runner.by_id:
                    return self._json({"error": "unknown step"}, 404)
                runner.set_enabled(sid, body.get("enabled"))
                core.save_settings(steps)
                return self._json(runner.state())
            if path.startswith("/api/step/"):
                parts = path.strip("/").split("/")   # api, step, <id>, <action>
                if len(parts) != 4 or parts[2] not in runner.by_id:
                    return self._json({"error": "unknown step"}, 404)
                step, action = runner.by_id[parts[2]], parts[3]
                if action == "start":
                    if runner.running_sequence:
                        return self._json({"error": "a Start all is in progress"}, 409)
                    threading.Thread(target=runner.start_step, args=(step,),
                                     daemon=True).start()
                    return self._json({"starting": step.id})
                if action == "stop":
                    threading.Thread(target=runner.stop_step, args=(step,),
                                     daemon=True).start()
                    return self._json({"stopping": step.id})
                return self._json({"error": "unknown action"}, 404)
            if path == "/api/quit":
                self._json({"quitting": True})
                threading.Thread(target=on_quit, daemon=True).start()
                return
            self._json({"error": "not found"}, 404)

    return Handler


def serve(runner, steps, port=PANEL_PORT, open_browser=True):
    done = threading.Event()

    def quit_all():
        runner.stop_all()
        done.set()

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(runner, steps, quit_all))
    except OSError:
        url = f"http://127.0.0.1:{port}/"
        print(f"The launcher already seems to be running: opening {url}")
        if open_browser:
            webbrowser.open(url)
        return
    url = f"http://127.0.0.1:{httpd.server_port}/"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"RecallAI launcher: control panel at {url}")
    print("Keep this window open. Ctrl+C (or 'Quit launcher' in the panel) stops everything.")
    if open_browser:
        webbrowser.open(url)
    try:
        while not done.wait(0.5):
            pass
    except KeyboardInterrupt:
        print("\nStopping everything...")
        runner.stop_all()
    finally:
        httpd.shutdown()
    print("All processes stopped. Bye.")


def run_headless(runner):
    print("Starting:", ", ".join(s.name for s in runner.steps if s.enabled) or "(nothing enabled)")
    last = {}
    runner.start_all()
    try:
        while True:
            for s in runner.state()["steps"]:
                key = (s["status"], s["message"])
                if s["enabled"] and last.get(s["id"]) != key:
                    last[s["id"]] = key
                    print(f"  [{s['status']:>15}] {s['name']}"
                          + (f" - {s['message']}" if s["message"] else ""))
            if not runner.running_sequence and runner._thread and not runner._thread.is_alive():
                if any(s.status == "failed" for s in runner.steps):
                    print("A step failed; logs are in launcher/logs/. Stopping everything.")
                    runner.stop_all()
                    return 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping everything...")
        runner.stop_all()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="RecallAI launcher")
    ap.add_argument("--run", action="store_true", help="run enabled steps without the panel")
    ap.add_argument("--only", help="comma-separated step ids to enable for this run")
    ap.add_argument("--list", action="store_true", help="list steps and exit")
    ap.add_argument("--port", type=int, default=PANEL_PORT)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    steps = core.load_steps()
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        unknown = wanted - {s.id for s in steps}
        if unknown:
            ap.error(f"unknown step(s): {', '.join(sorted(unknown))}")
        for s in steps:
            s.enabled = s.id in wanted
            s.status = "waiting" if s.enabled else "off"
    if args.list:
        for i, s in enumerate(steps, 1):
            print(f"{i}. [{'x' if s.enabled else ' '}] {s.id:<15} {s.name} - {s.description}")
        return 0

    runner = core.Runner(steps, opener=None if args.no_browser else webbrowser.open)
    missing = [k for k, v in runner.tools.items() if not v]
    if missing:
        print("Note: not found on this computer:", ", ".join(missing),
              "(steps that need them will explain how to install)")
    if args.run:
        return run_headless(runner)
    serve(runner, steps, port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    sys.exit(main())
