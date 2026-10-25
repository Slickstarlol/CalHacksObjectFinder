"""RecallAI launcher engine: run the project's processes one after another.

Each step (see steps.json) is a command plus a way to know it is ready:
  ready.type = "http"   -> poll a URL until it answers (server, dashboards, SpacetimeDB)
  ready.type = "port"   -> poll a TCP port
  ready.type = "exit"   -> one-shot command; ready when it exits with code 0
  ready.type = "alive"  -> ready if the process is still running after N seconds (camera)
Steps run strictly in order: the next starts only when the previous is ready.
If a step fails, the remaining steps are skipped and nothing else is started.

A step can have options: a dropdown in the panel when "choices" names a list
the launcher can look up (CHOICE_COMMANDS, e.g. which camera the Camera step
uses), otherwise a text box. {"id", "label", "flag", "default", "choices",
"dotenv", "secret"}. The chosen value is passed as `flag value`, or as a plain
argument when there is no flag, and remembered in settings.local.json.
"dotenv": NAME keeps the value in the repo's .env as NAME=value instead (the
backend reads it from there; nothing is passed on the command line), e.g. the
Grok API key. "secret": the panel only ever gets a masked hint of the value.

Pure standard library so it runs with any Python and freezes into an .exe.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

IS_WINDOWS = os.name == "nt"

# launcher/ folder (also correct when frozen into launcher/RecallAI.exe)
if getattr(sys, "frozen", False):
    LAUNCHER_DIR = Path(sys.executable).resolve().parent
else:
    LAUNCHER_DIR = Path(__file__).resolve().parent
REPO_ROOT = LAUNCHER_DIR.parent
STEPS_FILE = LAUNCHER_DIR / "steps.json"
SETTINGS_FILE = LAUNCHER_DIR / "settings.local.json"   # your toggles (git-ignored)
LOG_DIR = LAUNCHER_DIR / "logs"


def env_file_path(repo_root=REPO_ROOT):
    """The .env the backend reads (same rule as backend/recallai/paths.py)."""
    override = os.environ.get("RECALLAI_ENV_FILE", "").strip()
    return Path(override).expanduser().resolve() if override else Path(repo_root) / ".env"


ENV_FILE = env_file_path()   # secrets such as XAI_API_KEY (git-ignored)

# Lists a dropdown can offer, each printed as JSON by a backend command.
# cameras: [{"index": 0, "width": 1280, "height": 720}, ...]
CHOICE_COMMANDS = {
    "cameras": ["{python}", "-m", "recallai.vision.cameras", "--json"],
}
CHOICE_TIMEOUT = 45

STATES = ("off", "waiting", "starting", "ready", "done", "already-running",
          "failed", "skipped", "stopped", "exited")
ACTIVE = {"starting", "ready"}


# ---------------------------------------------------------------------------
# Tools: which python / npm / spacetime to use
# ---------------------------------------------------------------------------
def find_python():
    """The Python that runs the backend. When frozen into an .exe,
    sys.executable is the exe itself, so look for a real interpreter."""
    override = os.environ.get("RECALLAI_PYTHON")
    if override:
        return override
    if not getattr(sys, "frozen", False):
        return sys.executable
    for name in ("python", "py", "python3"):
        path = shutil.which(name)
        if path and "WindowsApps" not in path:   # skip the Store stub
            return path
    return "python"


def find_tool(name):
    """npm / spacetime; on Windows npm is npm.cmd."""
    candidates = [f"{name}.cmd", f"{name}.exe", name] if IS_WINDOWS else [name]
    for c in candidates:
        path = shutil.which(c)
        if path:
            return path
    if name == "spacetime" and IS_WINDOWS:   # default install location
        guess = Path(os.environ.get("LOCALAPPDATA", "")) / "SpacetimeDB" / "spacetime.exe"
        if guess.exists():
            return str(guess)
    return None


def tool_paths():
    return {"python": find_python(), "npm": find_tool("npm"),
            "spacetime": find_tool("spacetime")}


# ---------------------------------------------------------------------------
# .env: KEY=value lines, read the same way as backend/recallai/ai/llm.py
# ---------------------------------------------------------------------------
def _dotenv_key(line):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    return line.split("=", 1)[0].strip().removeprefix("export ").strip()


def read_dotenv(path):
    """{KEY: value}; the first non-empty value of a key wins, as in the backend."""
    values = {}
    try:
        text = Path(path).read_text(encoding="utf-8-sig")   # tolerate Notepad's BOM
    except OSError:
        return values
    for line in text.splitlines():
        key = _dotenv_key(line)
        value = line.split("=", 1)[1].strip().strip('"').strip("'") if key else ""
        if key and value:
            values.setdefault(key, value)
    return values


def write_dotenv(path, key, value):
    """Set KEY=value (replacing its first line, if any), keeping every other line."""
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        lines = []
    for i, line in enumerate(lines):
        if _dotenv_key(line) == key:
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mask(secret):
    """Enough of a secret to recognise it, not to use it (check_grok prints the same)."""
    return f"{secret[:8]}…{secret[-4:]}" if len(secret) > 16 else ("•" * 8 if secret else "")


# ---------------------------------------------------------------------------
# Readiness checks
# ---------------------------------------------------------------------------
def http_ok(url, timeout=1.5):
    """True if the URL answers with any HTTP status < 500. Tries localhost
    over IPv4 and IPv6 (Vite on Windows often listens on ::1 only)."""
    urls = [url]
    if "://localhost" in url:
        urls += [url.replace("://localhost", "://127.0.0.1"),
                 url.replace("://localhost", "://[::1]")]
    for u in urls:
        try:
            with urllib.request.urlopen(u, timeout=timeout) as r:
                if r.status < 500:
                    return True
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return True
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return False


def port_open(port, host="localhost", timeout=1.0):
    for h in ("127.0.0.1", "::1") if host == "localhost" else (host,):
        fam = socket.AF_INET6 if ":" in h else socket.AF_INET
        try:
            with socket.socket(fam, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                if s.connect_ex((h, int(port))) == 0:
                    return True
        except OSError:
            continue
    return False


def check_ready(spec):
    t = spec.get("type")
    if t == "http":
        return http_ok(spec["url"])
    if t == "port":
        return port_open(spec["port"], spec.get("host", "localhost"))
    return False


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
@dataclass
class Step:
    id: str
    name: str
    description: str
    command: list
    cwd: str = "."
    enabled: bool = False
    ready: dict = field(default_factory=lambda: {"type": "exit"})
    timeout: float = 60
    open_url: str = ""
    install_if_missing: str = ""      # e.g. "node_modules" -> run `npm install` first
    needs: list = field(default_factory=list)   # tools: python / npm / spacetime
    options: list = field(default_factory=list)  # dropdowns, see the module docstring
    values: dict = field(default_factory=dict)   # option id -> chosen value
    # runtime
    status: str = "off"
    message: str = ""
    pid: int | None = None
    proc: subprocess.Popen | None = field(default=None, repr=False)
    log: deque = field(default_factory=lambda: deque(maxlen=400), repr=False)
    log_count: int = 0
    started_at: float | None = None

    def public(self):
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "enabled": self.enabled, "status": self.status, "message": self.message,
            "pid": self.pid, "open_url": self.open_url,
            "command": " ".join(self.command), "cwd": self.cwd,
            "kind": self.ready.get("type"), "log_count": self.log_count,
            "options": [self._public_option(o) for o in self.options],
        }

    def _public_option(self, o):
        value = self.values.get(o["id"], "")
        out = {"id": o["id"], "label": o.get("label", o["id"]),
               "choices": o.get("choices"), "value": value}
        if o.get("dotenv"):
            out["dotenv"] = o["dotenv"]
        if o.get("secret"):   # the panel gets a hint, never the secret itself
            out.update(value="", secret=True, hint=mask(value))
        return out

    def option_args(self):
        """[flag, value, ...] for every option with a value; a plain argument
        when there is no flag (required); nothing for values kept in .env."""
        args = []
        for o in self.options:
            value = str(self.values.get(o["id"], "")).strip()
            if o.get("dotenv"):
                continue
            if o.get("flag"):
                if value:
                    args += [o["flag"], value]
            elif value:
                args.append(value)
            else:
                raise ValueError(f"{o.get('label', o['id'])} is empty")
        return args

    def saved_values(self):
        """Option values for settings.local.json (values kept in .env are left out)."""
        return {o["id"]: self.values.get(o["id"], "") for o in self.options
                if not o.get("dotenv")}


def load_steps(steps_file=None, settings_file=None, env_file=None):
    steps_file = steps_file or STEPS_FILE
    settings_file = settings_file or SETTINGS_FILE
    data = json.loads(Path(steps_file).read_text(encoding="utf-8"))
    steps = [Step(**{k: v for k, v in s.items() if k in Step.__dataclass_fields__})
             for s in data["steps"]]
    ids = [s.id for s in steps]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate step ids in steps.json")
    dotenv = read_dotenv(env_file or ENV_FILE)
    for s in steps:
        s.values = {o["id"]: dotenv.get(o["dotenv"], "") if o.get("dotenv")
                    else str(o.get("default", "")) for o in s.options}
    try:
        saved = json.loads(Path(settings_file).read_text(encoding="utf-8"))
        for s in steps:
            if s.id in saved.get("enabled", {}):
                s.enabled = bool(saved["enabled"][s.id])
            keep = s.saved_values()
            for oid, value in (saved.get("options", {}).get(s.id) or {}).items():
                if oid in keep and str(value).strip():
                    s.values[oid] = str(value)
    except (OSError, ValueError, AttributeError):
        pass
    for s in steps:
        s.status = "waiting" if s.enabled else "off"
    return steps


def save_settings(steps, settings_file=None):
    options = {s.id: s.saved_values() for s in steps}
    try:
        Path(settings_file or SETTINGS_FILE).write_text(json.dumps(
            {"enabled": {s.id: s.enabled for s in steps},
             "options": {sid: v for sid, v in options.items() if v}}, indent=2),
            encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
class Runner:
    def __init__(self, steps, repo_root=REPO_ROOT, log_dir=LOG_DIR, tools=None,
                 poll=0.5, opener=None, env=None, env_file=None):
        self.steps = steps
        self.by_id = {s.id: s for s in steps}
        self.repo_root = Path(repo_root)
        self.log_dir = Path(log_dir)
        self.env_file = Path(env_file) if env_file else env_file_path(self.repo_root)
        self.tools = tools if tools is not None else tool_paths()
        self.poll = poll
        self.opener = opener                 # callable(url) to open a browser tab
        self.extra_env = env or {}
        self.lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread = None
        self.running_sequence = False

    # -- helpers ------------------------------------------------------------
    def _log(self, step, line):
        line = line.rstrip("\r\n")
        with self.lock:
            step.log.append(line)
            step.log_count += 1
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with open(self.log_dir / f"{step.id}.log", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def _set(self, step, status, message=""):
        with self.lock:
            step.status, step.message = status, message
        self._log(step, f"[launcher] {status}{': ' + message if message else ''}")

    def _expand(self, step):
        missing = [t for t in step.needs if not self.tools.get(t)]
        if missing:
            raise FileNotFoundError(
                f"{', '.join(missing)} not found. " + {
                    "npm": "Install Node.js from https://nodejs.org",
                    "spacetime": "Install SpacetimeDB: iwr https://windows.spacetimedb.com -useb | iex",
                    "python": "Install Python 3.10+",
                }.get(missing[0], ""))
        return [str(self.tools.get(tok[1:-1], tok)) if tok.startswith("{") and tok.endswith("}")
                else tok for tok in step.command] + step.option_args()

    def _env(self):
        env = dict(os.environ)
        backend = str(self.repo_root / "backend")
        env["PYTHONPATH"] = backend + (os.pathsep + env["PYTHONPATH"]
                                       if env.get("PYTHONPATH") else "")
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["FORCE_COLOR"] = "0"
        env["NO_COLOR"] = "1"
        # A value saved in .env from the panel beats an older one in the shell
        # (the backend lets a real environment variable override .env).
        for s in self.steps:
            for o in s.options:
                if o.get("dotenv") and s.values.get(o["id"]):
                    env.pop(o["dotenv"], None)
        env.update(self.extra_env)
        return env

    def _spawn(self, step, argv, cwd):
        kwargs = dict(cwd=str(cwd), env=self._env(), stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                      text=True, encoding="utf-8", errors="replace", bufsize=1)
        if IS_WINDOWS:
            kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                       | subprocess.CREATE_NO_WINDOW)
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(argv, **kwargs)
        threading.Thread(target=self._pump, args=(step, proc), daemon=True).start()
        return proc

    def _pump(self, step, proc):
        for line in proc.stdout:
            self._log(step, line)

    def _alive(self, step):
        return step.proc is not None and step.proc.poll() is None

    # -- one step -------------------------------------------------------------
    def start_step(self, step):
        """Start a step and wait until it is ready. Returns True on success."""
        cwd = self.repo_root / step.cwd
        spec = step.ready
        # Already running (e.g. you started the server yourself)? Don't start twice.
        if spec.get("type") in ("http", "port") and check_ready(spec):
            self._set(step, "already-running", "already up, not starting a second copy")
            self._maybe_open(step)
            return True
        try:
            argv = self._expand(step)
            if step.install_if_missing and not (cwd / step.install_if_missing).exists():
                self._set(step, "starting", "first run: npm install (can take a minute)")
                inst = self._spawn(step, [self.tools["npm"], "install"], cwd)
                step.proc, step.pid = inst, inst.pid
                while inst.poll() is None:
                    if self._cancel.wait(self.poll):
                        self.stop_step(step)
                        return False
                if inst.returncode != 0:
                    self._set(step, "failed", f"npm install exited with {inst.returncode}")
                    return False
            self._set(step, "starting", " ".join(argv))
            step.proc = self._spawn(step, argv, cwd)
            step.pid, step.started_at = step.proc.pid, time.time()
        except (OSError, ValueError) as e:
            self._set(step, "failed", str(e))
            return False

        deadline = time.time() + float(step.timeout)
        kind = spec.get("type", "exit")
        while True:
            if self._cancel.is_set():
                self.stop_step(step)
                return False
            code = step.proc.poll()
            if kind == "exit":
                if code is not None:
                    if code == 0:
                        self._set(step, "done")
                        self._maybe_open(step)
                        return True
                    self._set(step, "failed", f"exited with code {code}")
                    return False
            else:
                if code is not None:
                    self._set(step, "failed", f"stopped unexpectedly (exit code {code}); see log")
                    return False
                if kind == "alive":
                    if time.time() - step.started_at >= float(spec.get("seconds", 5)):
                        self._set(step, "ready", "running")
                        return True
                elif check_ready(spec):
                    self._set(step, "ready", spec.get("url") or f"port {spec.get('port')}")
                    self._maybe_open(step)
                    return True
            if time.time() > deadline:
                self.stop_step(step)
                self._set(step, "failed", f"not ready after {step.timeout:g}s; see log")
                return False
            time.sleep(self.poll)

    def _maybe_open(self, step):
        if step.open_url and self.opener:
            try:
                self.opener(step.open_url)
            except Exception:
                pass

    def stop_step(self, step):
        proc = step.proc
        if proc is not None and proc.poll() is None:
            try:
                if IS_WINDOWS:   # kill the whole tree (npm -> node, etc.)
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=15)
                else:
                    os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    if not IS_WINDOWS:
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                except (OSError, ProcessLookupError):
                    pass
            if step.status in ACTIVE or step.status == "starting":
                self._set(step, "stopped")
        with self.lock:
            step.pid = None if proc is None or proc.poll() is not None else step.pid

    # -- the whole sequence -------------------------------------------------
    def run_sequence(self):
        """Run every enabled step in order; stop at the first failure."""
        self._cancel.clear()
        self.running_sequence = True
        try:
            enabled = [s for s in self.steps if s.enabled]
            for s in enabled:
                if s.status not in ACTIVE and s.status != "already-running":
                    self._set(s, "waiting")
            for i, s in enumerate(enabled):
                if self._cancel.is_set():
                    break
                if s.status in ACTIVE and self._alive(s):
                    continue   # still running from before
                if not self.start_step(s):
                    for later in enabled[i + 1:]:
                        self._set(later, "skipped", f"because '{s.name}' did not start")
                    return False
            return not self._cancel.is_set()
        finally:
            self.running_sequence = False

    def start_all(self):
        if self._thread and self._thread.is_alive():
            return False
        self._thread = threading.Thread(target=self.run_sequence, daemon=True)
        self._thread.start()
        return True

    def stop_all(self):
        """Stop everything, last step first."""
        self._cancel.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=20)
        for s in reversed(self.steps):
            self.stop_step(s)
        for s in self.steps:
            if s.status in ("waiting",):
                self._set(s, "off" if not s.enabled else "stopped")

    def watch(self):
        """Mark long-running steps that died on their own (call periodically)."""
        for s in self.steps:
            if s.status == "ready" and s.ready.get("type") != "exit" and s.proc \
                    and s.proc.poll() is not None:
                self._set(s, "exited", f"process ended (code {s.proc.returncode}); see log")

    # -- dropdowns -----------------------------------------------------------
    def choices(self, name):
        """Look up a dropdown list, e.g. the cameras on this computer.
        Returns {"choices": [{"value", "label"}], "error": str | None}."""
        if name not in CHOICE_COMMANDS:
            raise KeyError(name)
        users = [(s, o) for s in self.steps for o in s.options if o.get("choices") == name]
        # Don't open a camera a running step is using; list it as in use.
        busy = {s.values.get(o["id"]) for s, o in users if s.status in ACTIVE and self._alive(s)}
        busy.discard(None)
        argv = [str(self.tools.get(t[1:-1], t)) if t.startswith("{") and t.endswith("}") else t
                for t in CHOICE_COMMANDS[name]]
        for value in sorted(busy):
            argv += ["--skip", value]
        try:
            out = subprocess.run(argv, cwd=str(self.repo_root), env=self._env(),
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=CHOICE_TIMEOUT,
                                 creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0)
            found = json.loads(out.stdout.strip().splitlines()[-1]) if out.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
            found = None
        if found is None:
            return {"choices": [{"value": v, "label": f"Camera {v} (in use)"} for v in sorted(busy)],
                    "error": "Couldn't look for cameras. Is the backend installed "
                             "(pip install -r backend/requirements.txt)?"}
        listed = [{"value": str(c["index"]),
                   "label": f"Camera {c['index']} · {c['width']}×{c['height']}"} for c in found]
        listed += [{"value": v, "label": f"Camera {v} (in use)"} for v in sorted(busy)]
        listed.sort(key=lambda c: int(c["value"]) if c["value"].isdigit() else 99)
        return {"choices": listed, "error": None}

    def set_option(self, step_id, option_id, value):
        """Choose an option value (empty = the default). A running step restarts
        to use it; a "dotenv" value is written to .env. Raises ValueError for a
        value that can't be used."""
        s = self.by_id[step_id]
        if option_id not in s.values:
            raise KeyError(option_id)
        o = next(o for o in s.options if o["id"] == option_id)
        label = o.get("label", option_id)
        value = str(value).strip() or str(o.get("default", ""))
        if not value:
            raise ValueError(f"{label} can't be empty")
        # One command-line argument / one .env line: no flags, spaces or newlines.
        if value.startswith("-") or any(c.isspace() for c in value):
            raise ValueError(f"{label} can't start with '-' or contain spaces")
        if o.get("dotenv"):
            try:
                write_dotenv(self.env_file, o["dotenv"], value)
            except OSError as e:
                raise ValueError(f"couldn't save {self.env_file}: {e}") from e
        with self.lock:
            changed = s.values[option_id] != value
            s.values[option_id] = value
        if changed and s.status in ACTIVE and self._alive(s) and not self.running_sequence:
            self._log(s, f"[launcher] {option_id} changed to {value}: restarting")
            threading.Thread(target=self._restart, args=(s,), daemon=True).start()
            return s, True
        return s, False

    def _restart(self, step):
        self.stop_step(step)
        self.start_step(step)

    def set_enabled(self, step_id, enabled):
        s = self.by_id[step_id]
        with self.lock:
            s.enabled = bool(enabled)
            if s.status in ("off", "waiting", "skipped", "stopped", "failed", "exited", "done"):
                s.status = "waiting" if s.enabled else "off"
                s.message = ""
        return s

    def state(self):
        self.watch()
        with self.lock:
            return {"steps": [s.public() for s in self.steps],
                    "running": self.running_sequence,
                    "tools": {k: bool(v) for k, v in self.tools.items()},
                    "repo": str(self.repo_root)}

    def logs(self, step_id, since=0):
        s = self.by_id[step_id]
        with self.lock:
            lines = list(s.log)
            first = s.log_count - len(lines)
            start = max(0, int(since) - first)
            return {"lines": lines[start:], "next": s.log_count}
