# Team workflow

## Where each part lives

| Part | Folder | Owner area | Checked in CI by |
|---|---|---|---|
| Python app (camera, API, Grok, bridge) | `backend/recallai/` | backend | `pytest` on Python 3.10 + 3.12 (fast), 3.12 (slow) |
| Python tests + fixtures | `backend/tests/` | backend | same |
| SpacetimeDB module | `spacetimedb/` | module | `npm ci`, `tsc --noEmit`, `vitest` |
| Live dashboard (React + TS, SpacetimeDB) | `frontend/live-dashboard/` | live dashboard | `npm ci`, `npm run build`, `vitest` |
| Camera / people dashboard (React JSX) | `frontend/dashboard/` | Chawin | `npm ci`, `npm run build`, `npm run lint` |
| Registered items (committed photos) | `data/belongings/` | everyone | - |
| Runtime data (git-ignored) | `data/memory.db`, `data/snapshots/` | local only | - |
| YOLO weights | `models/` | - | - |
| Dev helpers, data migration | `scripts/` | - | `migrate_layout.py` is tested by pytest |
| CI | `.github/workflows/ci.yml` | - | - |

## Branch → PR → CI → merge

1. Update and branch from the main branch:
   `git switch main; git pull; git switch -c <you>/<short-topic>` (e.g. `chawin/people-edit`).
2. Commit small, focused changes. Never commit `.env`, `.spacetime_token`,
   `data/memory.db*`, `data/snapshots/`, `node_modules/` or `dist/` (all git-ignored).
3. Run the checks for the parts you touched (below), then `git push -u origin HEAD`.
4. Open a pull request. The template has a short checklist; CI runs every job
   on each push to the PR.
5. Get one teammate to review. Merge only when CI is green
   (squash-merge keeps main readable). Delete the branch after merging.
6. If main moved on, `git pull --rebase origin main` (or merge main in), re-run
   the checks, push again.

## Run and test locally (from the repository root)

One-time setup (Windows PowerShell; `scripts/dev.sh` takes the same commands on macOS/Linux):
```
.\scripts\dev.ps1 setup     # pip install, pip install -e backend, npm install x3, migrate old data
```

| What | Command | Raw command |
|---|---|---|
| API + search page | `.\scripts\dev.ps1 server` | `python -m uvicorn recallai.api.server:app --host 0.0.0.0 --port 8000` |
| Camera | `.\scripts\dev.ps1 camera` | `python -m recallai.vision.webcam` |
| Register items | `.\scripts\dev.ps1 register --list` | `python -m recallai.vision.register --list` |
| Ask the agent | `.\scripts\dev.ps1 ask "where is my mouse?"` | `python -m recallai.ai.agent "where is my mouse?"` |
| Check Grok key | `.\scripts\dev.ps1 check-grok` | `python -m recallai.ai.check_grok` |
| SpacetimeDB server | `.\scripts\dev.ps1 spacetime` | `spacetime start` |
| Publish the module | `.\scripts\dev.ps1 publish` | `cd spacetimedb; spacetime publish --server local recallai` |
| One-off sync | `.\scripts\dev.ps1 sync-once` | `python -m recallai.sync.spacetime_bridge --once` |
| Camera dashboard | `.\scripts\dev.ps1 dashboard` | `cd frontend/dashboard; npm run dev` (:5173) |
| Live dashboard | `.\scripts\dev.ps1 live-dashboard` | `cd frontend/live-dashboard; npm run dev` (:5174) |
| All tests | `.\scripts\dev.ps1 test` | see below |

The `python -m recallai...` commands need `backend/` on the import path: either
`pip install -e backend` once (done by `setup`), or `$env:PYTHONPATH="backend"`
in that terminal. The dev scripts set `PYTHONPATH` themselves, and pytest gets it
from `pytest.ini`.

Tests per part:
```
python -m pytest -m "not slow"     # backend, fast (Grok mocked, no downloads)
python -m pytest                   # backend, everything (YOLO + DINOv2 on real frames)
cd spacetimedb;             npx tsc --noEmit -p .; npx vitest run
cd frontend/live-dashboard; npm run build; npx vitest run
cd frontend/dashboard;      npm run build; npm run lint
```

## Rules that keep the parts in sync

- **Changed the SpacetimeDB module (`spacetimedb/src/index.ts`)? Regenerate the
  bindings in the same PR:** `.\scripts\dev.ps1 generate`
  (= `cd frontend/live-dashboard; npm run spacetime:generate`), commit
  `frontend/live-dashboard/src/module_bindings/`, and update
  [SPACETIME_CONTRACT.md](SPACETIME_CONTRACT.md) and the bridge
  (`backend/recallai/sync/spacetime_bridge.py`) if columns or reducers changed.
  Add columns at the end with a default so `spacetime publish` upgrades in place.
- **Paths go through `backend/recallai/paths.py`.** Don't build paths from
  `__file__` in other modules. Image paths in SQLite stay relative to `data/`
  (`snapshots/<file>`).
- **Changed the database schema (`recallai/memory/db.py`)?** Keep `init_db`
  upgrading an existing `memory.db` in place, and add a test like the ones in
  `backend/tests/test_db.py`.
- **Pulled the reorganised layout onto an old checkout?** Run
  `python scripts/migrate_layout.py` once (moves root-level `memory.db*`,
  `snapshots/` and untracked `belongings/` files into `data/`; never overwrites).
