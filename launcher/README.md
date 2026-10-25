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
| 5 | Camera | **on** | still running after 20 s (runs with `--no-window`: no OpenCV window, the live dashboard shows the feed) |
| 6 | Live dashboard (SpacetimeDB) | **on** | http://localhost:5174 answers (opens in browser) |

- Flip the switch on each step you want, then **Start all**. Steps start top to
  bottom; each one waits until the previous one is ready. If one fails, the rest
  are skipped and its log tells you why.
- Each step also has its own **Start** / **Stop** button and a live **Log**
  (also saved in `launcher/logs/<step>.log`).
- The **Camera** step has an **Output** dropdown listing the cameras on this
  computer (↻ looks again). The choice is passed as `--camera N` and
  remembered; changing it while the camera runs restarts it.
- The **Check Grok key** step has an **API key** box: paste a new xAI key and
  **Save**. It is written to `XAI_API_KEY` in `.env` at the repo root
  (git-ignored), which the server and camera read too, and it beats an older
  `XAI_API_KEY` set in your shell for everything the launcher starts. The panel
  only shows its first and last characters. A running server or camera keeps the
  old key until you restart it.
- The **Publish SpacetimeDB module** step has a **Maincloud database** box: the
  database name or identity `spacetime publish` uploads to (default
  `c200c46c…6292`, the identity of `calhacks-c3s7r`). Remembered like the
  switches; clearing it and saving goes back to the default. The server and
  live dashboard still use `SPACETIME_DB` / `VITE_SPACETIMEDB_DB_NAME`
  (default `calhacks-l28u4`), not this box.
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
(`{"id", "label", "flag", "default", "choices", "dotenv", "secret"}`: a dropdown
when `choices` names a list, otherwise a text box; passed as `flag value`, or as
a plain argument without a flag; `"dotenv": "NAME"` keeps the value in `.env`
instead of passing it, `"secret": true` never sends it back to the panel; never
put a secret's value in `steps.json`, it is in git), and a `ready` check:
`{"type": "http", "url": ...}`, `{"type": "port", "port": ...}`,
`{"type": "exit"}` (one-shot) or `{"type": "alive", "seconds": N}`.
