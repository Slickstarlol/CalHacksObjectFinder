# Architecture

```
 webcam ──▶ recallai.vision.webcam ──────────────────────────────┐
            YOLO (models/yolo26n.pt)                              │ annotated frame
            → recognition: "is it mine?" (DINOv2 vs data/belongings/)   (live_feed → POST /api/live)
            → people_id: "who is it?" (DINOv2 vs data/people/)    │
            → recorder: seen / left_view                          │
                 │                                                ▼
                 ▼ writes                                 recallai.api.server (FastAPI :8000)
   data/memory.db (SQLite, primary store)  ◀──── reads ────  /api/auth/*, /search, / (GPS), /api/*, /snapshots/*, /app/
   data/snapshots/ (frames + crops)                               │        ▲
                 │                                                │        │ /api, /snapshots (Vite proxy)
                 │  recallai.sync.spacetime_bridge                │   frontend/dashboard (:5173)
                 │  (thread inside the server, SPACETIME_SYNC=0 off)   camera feed, enrollment, people
                 ▼
   SpacetimeDB "recallai" (:3000, module in spacetimedb/)
                 │  live WebSocket subscription
                 ▼
   frontend/live-dashboard (:5174, or built into /app/ on :8000)
   belongings, sightings, alerts (dismiss → copied back to SQLite), people, Grok chat
```

## Grok (xAI) features: `recallai.ai`

| Module | Job |
|---|---|
| `llm.py` | OpenAI SDK client pointed at `https://api.x.ai/v1`; reads `XAI_API_KEY` from the environment or the root `.env` |
| `interpreter.py` | Background thread: Grok checks each new sighting against your reference photos and describes where it is |
| `agent.py` | Tool-using agent behind `POST /api/ask` (list belongings, search sightings, last seen, look at a photo) |
| `watcher.py` | Every minute inside the server: rules + Grok flag items you may have forgotten (alerts) |
| `check_grok.py` | Step-by-step setup check (`python -m recallai.ai.check_grok`) |

Without a key everything else still runs; Grok features are skipped.

## Paths

`backend/recallai/paths.py` is the only place that knows where files live
(`data/`, `models/`, static pages, `frontend/live-dashboard/dist`, `.env`,
`.spacetime_token`). Every path can be overridden with a `RECALLAI_*`
environment variable (see the module docstring). SQLite stores image paths
relative to `data/` (`snapshots/<file>`), which is also the URL served at
`/snapshots/<file>`.

The data contract between SQLite, the bridge, the module and the live dashboard
is in [SPACETIME_CONTRACT.md](SPACETIME_CONTRACT.md).

## Accounts

Users and sign-in sessions live in `data/memory.db` (`recallai/memory/users.py`,
routes in `recallai/api/auth.py`). Every belonging (`info.json` `owner_id`),
sighting, alert and person carries an owner; every user-data endpoint needs
`Authorization: Bearer <token>` and filters by the caller's id
(`recallai/owners.py` holds the shared filter helpers). The camera is a
trusted local device, not a user. Full description: [AUTH.md](AUTH.md).
