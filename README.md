# RecallAI – CalHacks Object Finder

Remembers where **your** belongings were last seen. YOLO finds objects, DINOv2
checks whether each one is yours (compared with photos you register), and each
sighting is saved with a snapshot, a close-up, nearby objects, and optional
phone GPS. Grok (xAI) describes where things are, answers questions, and flags
items you may have forgotten. Each person signs in with their own account and
only sees their own belongings, sightings, alerts and people ([docs/AUTH.md](docs/AUTH.md)).

```
backend/          Python app: the recallai package + tests
  recallai/
    paths.py      every filesystem path (data/, models/, .env ...), env-overridable
    api/          FastAPI server, search API, static pages (search.html, gps.html)
    vision/       webcam loop, recognition, recorder, register, enrollment, live feed
    memory/       SQLite schema, accounts (users.py), time/duration math, people
    ai/           Grok client, interpreter, agent, watcher, check_grok
    sync/         SQLite -> SpacetimeDB bridge
  tests/          pytest suite + fixtures
frontend/
  dashboard/      camera / enrollment / people dashboard (React JSX, :5173)
  live-dashboard/ live SpacetimeDB dashboard (React + TS, :5174, built into :8000/app/)
spacetimedb/      SpacetimeDB module (tables + reducers)
data/             belongings/ (committed photos); memory.db and snapshots/ (git-ignored)
models/           yolo26n.pt
docs/             ARCHITECTURE.md, WORKFLOW.md, SPACETIME_CONTRACT.md, AUTH.md
scripts/          dev.ps1 / dev.sh helpers, migrate_layout.py
launcher/         RecallAI.bat control panel: start/stop each step (see launcher/README.md)
```

**Quick start:** double-click `launcher\RecallAI.bat`. A control panel opens in your
browser with a switch for each step (SpacetimeDB, server, camera, dashboards...);
**Start all** starts the switched-on steps one after another. See [launcher/README.md](launcher/README.md).

How the parts fit: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Branches, PRs, CI and the per-part test commands: [docs/WORKFLOW.md](docs/WORKFLOW.md).

## Setup (Windows PowerShell, from the repository root)
```
.\scripts\dev.ps1 setup      # pip install -r backend/requirements.txt, pip install -e backend, npm install x3
setx XAI_API_KEY "xai-..."   # or put XAI_API_KEY=... in .env at the repo root; key from https://console.x.ai
```
Without the dev script: `pip install -r backend/requirements.txt` and
`pip install -e backend` (or set `$env:PYTHONPATH="backend"` in each terminal).
The first run downloads DINOv2 (~90 MB). Without an API key everything still
runs; the Grok features are skipped. Check the key with `.\scripts\dev.ps1 check-grok`.

**Updating an older checkout?** Run `python scripts/migrate_layout.py` once. It
moves your root-level `memory.db*`, `snapshots/` and unregistered `belongings/`
files into `data/` (never overwrites; `.env` and `.spacetime_token` stay at the root).

## First launch: create your account
Start the server (step 2 below), open a dashboard (http://localhost:5173, or
http://localhost:8000/app/ after building the live dashboard) and choose
**Create an account**. Do this first: **the first account ever created takes
over everything recorded before accounts existed** (belongings in
`data/belongings/`, sightings, alerts, people). Later accounts start empty and
only ever see their own data. Every dashboard and page asks you to sign in;
**Sign out** is in the top bar. Details, endpoints and security notes:
[docs/AUTH.md](docs/AUTH.md).

## Use
1. **Register belongings**: in the camera dashboard press **Add** (5 camera
   angles; the item is yours), or from the command line (5–8 photos each,
   different angles and lighting):
   ```
   python -m recallai.vision.register --list                            # what's registered, owners, missing photos
   python -m recallai.vision.register --id 3617519a583a                 # add photos to "My Bag"
   python -m recallai.vision.register --user kent --name "My Keys" --category "cell phone"
   ```
   `--user` picks the owning account (optional when only one account exists;
   with none yet, the item is claimed by the first account).
   In the window: **S** saves an auto-cropped photo (green box), **B** lets you draw the box, **Q** finishes.
2. **Server** (accounts, search page, phone GPS, API, SpacetimeDB sync): `.\scripts\dev.ps1 server`
   (= `python -m uvicorn recallai.api.server:app --host 0.0.0.0 --port 8000`)
3. **Camera:** `.\scripts\dev.ps1 camera` (= `python -m recallai.vision.webcam`; `--camera 0`, `--threshold 0.75`).
   It's a trusted local device: on the same machine as the server it needs no
   login and tags each sighting with the owner of the belonging it saw. A camera
   on another machine needs `RECALLAI_DEVICE_KEY` (same value for both) and
   `RECALLAI_SERVER_URL`.
4. **Search:** http://localhost:8000/search (sign in), or ask from the terminal
   (unscoped, as the machine owner): `python -m recallai.ai.agent "where did I leave my mouse?"`.
   **Phone GPS:** open http://<laptop-ip>:8000/ on your phone and sign in; the
   location is stored for your account only.
5. **Camera dashboard:** `.\scripts\dev.ps1 dashboard` → http://localhost:5173
   (live camera, last locations, people; **Add** enrolls a new belonging from 5 camera angles).
6. **Live dashboard** (optional, SpacetimeDB), 4 terminals:
   ```
   .\scripts\dev.ps1 spacetime        # database on :3000 (install: iwr https://windows.spacetimedb.com -useb | iex)
   .\scripts\dev.ps1 publish          # once per module change
   .\scripts\dev.ps1 server           # API + bridge ("Dashboard sync: ...")
   .\scripts\dev.ps1 camera
   .\scripts\dev.ps1 live-dashboard   # http://localhost:5174
   ```
   Or `cd frontend/live-dashboard; npm run build` once and use http://localhost:8000/app/.
   From a phone on the same Wi-Fi: http://<laptop-ip>:5174 (or :8000/app/).

Settings (environment or `.env`): `XAI_API_KEY`, `RECALL_VISION_MODEL` /
`RECALL_AGENT_MODEL` (default `grok-4.7`), `SPACETIME_URL` (default
`http://localhost:3000`), `SPACETIME_DB` (default `recallai`), `SPACETIME_SYNC=0`
to turn the bridge off, `RECALLAI_DEVICE_KEY` / `RECALLAI_SERVER_URL` for a
camera on another machine, and `RECALLAI_DATA_DIR` etc. to move data (see
`backend/recallai/paths.py`). Accounts: `python -m recallai.memory.users list`,
`python -m recallai.memory.users claim <username>` (give ownerless data to an account).

Reset everything (also deletes all accounts): delete `data/memory.db`, then
`cd spacetimedb; spacetime publish --server local --delete-data=always recallai`
(the bridge re-registers and resyncs by itself). If the database was published by
someone else, republish with `--delete-data` (or delete `.spacetime_token` and republish).
Describe sightings saved before the key was set: `python -m recallai.ai.interpreter --backfill`.

## How it decides
| Event | When |
|---|---|
| `seen` | Item is in view; saved at most every 15 s |
| `left_view` | Item has been out of view for 5 s; keeps the **last frame it was visible in** |

Match threshold (DINOv2): 0.70. Measured on this project's own frames:
same item across frames 0.77–0.95, other chairs in the same room ≤0.64,
strangers' objects from COCO ≤0.50. If someone else's item gets labelled as
yours, raise `--threshold`. If yours is missed, add more reference photos.
YOLO confuses some classes (a gaming mouse up close came out as
"cell phone"/"remote"), so a belonging is also checked against related classes
(`CONFUSABLE_GROUPS` in `backend/recallai/vision/recognition.py`).

## Tests
```
.\scripts\dev.ps1 test        # everything below
python -m pytest -m "not slow"  # backend, fast (Grok mocked), no downloads
python -m pytest                # backend, incl. real-frame YOLO + DINOv2 tests
```
CI (`.github/workflows/ci.yml`) runs the backend on Python 3.10 and 3.12 plus
the module, live dashboard and camera dashboard checks on every push and PR.
