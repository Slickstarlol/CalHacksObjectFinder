# RecallAI launcher

Start the whole project from one place, step by step.

**Double-click `RecallAI.bat`** (or `RecallAI.exe` after building it). A control
panel opens in your browser at http://127.0.0.1:8765:

| # | Step | Default | Ready when |
|---|---|---|---|
| 1 | Check Grok key | off | `check_grok` exits OK |
| 2 | SpacetimeDB | off | http://localhost:3000/v1/ping answers |
| 3 | Publish SpacetimeDB module | off | `spacetime publish` exits OK |
| 4 | RecallAI server | **on** | http://localhost:8000/api/belongings answers (a 401 "sign in required" counts: the server is up) |
| 5 | Camera | **on** | still running after 20 s |
| 6 | Camera dashboard | **on** | http://localhost:5173 answers (opens in browser) |
| 7 | Live dashboard (SpacetimeDB) | off | http://localhost:5174 answers (opens in browser) |

- Flip the switch on each step you want, then **Start all**. Steps start top to
  bottom; each one waits until the previous one is ready. If one fails, the rest
  are skipped and its log tells you why.
- Each step also has its own **Start** / **Stop** button and a live **Log**
  (also saved in `launcher/logs/<step>.log`).
- The **Camera** step has an **Output** dropdown listing the cameras on this
  computer (↻ looks again). The choice is passed as `--camera N` and
  remembered; changing it while the camera runs restarts it.
- Something already running (e.g. you started the server yourself) is detected
  and not started twice.
- First run of a dashboard runs `npm install` automatically.
- **Stop all** stops everything (whole process trees, newest first).
  **Quit launcher** (or closing the black launcher window) stops everything too.
- Your switch choices are remembered in `launcher/settings.local.json` (not in git).

**First start:** when the dashboard opens, choose **Create an account**. The
first account takes over everything recorded before accounts existed; later
accounts only see their own belongings (see [docs/AUTH.md](../docs/AUTH.md)).
The Camera step needs no account: it runs on this machine as a trusted device.

## Without the browser panel
```
python launcher/launcher.py --list
python launcher/launcher.py --run                      # run the switched-on steps; Ctrl+C stops all
python launcher/launcher.py --run --only server,camera
```

## Make a real .exe (optional)
Double-click `build_exe.bat` (installs PyInstaller, builds `launcher/RecallAI.exe`).
Keep the exe in this folder next to `steps.json`. It still uses your installed
Python (`pip install -r backend/requirements.txt` first) to run RecallAI itself.
Set `RECALLAI_PYTHON` to pick a specific Python.

## Adding or changing steps
Edit `steps.json`. Each step has a `command` (`{python}`, `{npm}`, `{spacetime}`
are filled in), a `cwd` relative to the repo root, optional `options`
(dropdowns: `{"id", "label", "flag", "default", "choices"}`), and a `ready` check:
`{"type": "http", "url": ...}`, `{"type": "port", "port": ...}`,
`{"type": "exit"}` (one-shot) or `{"type": "alive", "seconds": N}`.
