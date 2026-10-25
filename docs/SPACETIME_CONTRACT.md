# RecallAI × SpacetimeDB — integration contract

Read this first. Every component (module, Python bridge, React dashboard) must
match it exactly. The SpacetimeDB **module source is the source of truth**:
`spacetimedb/src/index.ts` (already written and smoke-tested — do not change
table/column/reducer names or argument order without telling the lead).

## Changelog
- v2 (merge with people/enrollment): `belonging.tracking` (last column, default true),
  new table `person`, reducers `upsert_person` / `delete_person`. Existing arg orders unchanged
  except `upsert_belonging`, which gains a trailing `tracking` arg.
- v3 (accounts, see [AUTH.md](AUTH.md)): every mirrored table gains a last column
  `owner: string` (`default('')`, so a published database upgrades in place) = the
  RecallAI account's `users.id` as text, `""` = ownerless (recorded before accounts;
  re-pushed with the owner once the first account claims it). Every `upsert_*` reducer
  gains a trailing `owner` arg. The live dashboard subscribes with `WHERE owner = '<id>'`.
  The tables stay **public**: `owner` is filtering for a local app, not access control.
  The bindings in `frontend/live-dashboard/src/module_bindings/` were updated **by hand**
  for v3 (the `spacetime` CLI wasn't available); they match what `spacetime generate`
  emits for these columns (`owner: __t.string()`, no `.name()` needed). Regenerate with
  the CLI when you next have it to confirm.

## Architecture

```
webcam.py ─▶ SQLite data/memory.db (unchanged, still the primary store)
                 │
                 ▼  spacetime_bridge.py (background thread inside server.py)
          HTTP: POST {SPACETIME_URL}/v1/database/{DB}/call/{reducer}  (JSON array args, Bearer token)
                 ▼
        SpacetimeDB database "recallai"  (tables: belonging, sighting, alert, person)
                 │  live WebSocket subscription (spacetimedb/react)
                 ▼
        React dashboard (frontend/live-dashboard/, Vite + React + TS)
           - images & Grok chat come from FastAPI (http://localhost:8000): /snapshots/*, POST /api/ask
           - "Dismiss" alert -> reducer dismiss_alert (browser) -> bridge copies dismissal back to SQLite
```

Versions: npm `spacetimedb@2.13.0` (server module API `spacetimedb/server`, React hooks `spacetimedb/react`).
Defaults: SpacetimeDB at `http://localhost:3000` (WebSocket `ws://localhost:3000`), database name `recallai`,
FastAPI at `http://localhost:8000`.

## Tables (all public except `bridge`)

TypeScript keys are camelCase; SpacetimeDB column names (used in SQL) are snake_case.

### belonging — PK `id`
| key (TS) | SQL column | type | source (SQLite / disk) |
|---|---|---|---|
| id | id | string | `data/belongings/<id>/info.json` id |
| name | name | string | info.json name |
| category | category | string | info.json category |
| photos | photos | u32 | number of `reference_*` images |
| tracking | tracking | bool | info.json `tracking` (missing → true). **false while enrolling**: `enroll.py` creates the folder with `"tracking": false` and flips it to true once all 5 angles are saved; the matcher ignores it until then. `default(true)`, so an already-published database upgrades in place (no `--delete-data`). |
| owner | owner | string | info.json `owner_id` as text ("" if missing). Last column, `default('')`. |

The dashboard shows an **"Enrolling…"** badge (not "Not seen yet") for `tracking = false`.

### sighting — PK `id`, btree index on `belongingId`
| key (TS) | SQL column | type | from SQLite `sightings` |
|---|---|---|---|
| id | id | u64 | id |
| belongingId | belonging_id | string | belonging_id ("" if NULL — but only rows WITH a belonging_id are synced) |
| belongingName | belonging_name | string | belonging_name |
| objectName | object_name | string | object_name |
| event | event | string | event: 'seen' or 'left_view' (reducer rejects anything else) |
| seenAtMicros | seen_at_micros | i64 | timestamp (ISO 8601) → microseconds since epoch |
| imageUrl | image_url | string | "/" + image_path  (e.g. "/snapshots/1_x_seen.jpg") |
| cropUrl | crop_url | string | "/" + crop_path, or "" |
| description | description | string | description or "" (Grok's location sentence) |
| place | place | string | place or "" |
| nearby | nearby | string | nearby or "" ("laptop, bottle") |
| matchScore | match_score | f64 | match_score or 0 |
| verified | verified | i32 | verified: NULL→-1, 0→0, 1→1 |
| hasGps | has_gps | bool | latitude IS NOT NULL |
| latitude | latitude | f64 | latitude or 0 |
| longitude | longitude | f64 | longitude or 0 |
| owner | owner | string | owner_id as text, "" if NULL |

Sightings with `verified = 0` (Grok: not yours) ARE synced with verified=0; the dashboard hides them.

### alert — PK `id`
| key (TS) | SQL column | type | from SQLite `alerts` |
|---|---|---|---|
| id | id | u64 | id |
| belongingId | belonging_id | string | belonging_id |
| belongingName | belonging_name | string | belonging_name |
| sightingId | sighting_id | u64 | sighting_id or 0 |
| reason | reason | string | reason |
| urgency | urgency | string | urgency ('low' / 'high' / 'none') |
| message | message | string | message |
| createdAtMicros | created_at_micros | i64 | created_at (ISO) → microseconds |
| dismissed | dismissed | bool | dismissed != 0 |
| owner | owner | string | owner_id as text, "" if NULL |

Only alerts with `dismissed = 0` in SQLite need to be pushed initially, but pushing all is fine (idempotent).

### person — PK `id`
| key (TS) | SQL column | type | from SQLite `people` (managed by `people.py`, `/api/people`) |
|---|---|---|---|
| id | id | u64 | id |
| name | name | string | name (never empty; reducer rejects "") |
| description | description | string | description |
| relationship | relationship | string | relationship ("" if none) |
| knownSince | known_since | string | known_since `YYYY-MM-DD`, or "" if NULL |
| createdAtMicros | created_at_micros | i64 | created_at (ISO) → microseconds |
| updatedAtMicros | updated_at_micros | i64 | updated_at (ISO) → microseconds |
| owner | owner | string | owner_id as text, "" if NULL |

"How long you've known them" is derived from `knownSince` at read time (same rule as `people.known_for`).
All rows are synced; people deleted in SQLite are deleted here (same pruning as sightings/alerts).

## Reducers

HTTP calls send a **JSON array of arguments in exactly this order**. Python ints are fine for u64/i64/u32/i32.

| reducer | args (in order) | who may call |
|---|---|---|
| register_bridge | (none → body `[]`) | first caller becomes the bridge; same identity may repeat |
| upsert_belonging | id, name, category, photos, tracking, owner | bridge |
| delete_belonging | id | bridge |
| upsert_sighting | id, belongingId, belongingName, objectName, event, seenAtMicros, imageUrl, cropUrl, description, place, nearby, matchScore, verified, hasGps, latitude, longitude, owner | bridge |
| delete_sighting | id | bridge |
| upsert_alert | id, belongingId, belongingName, sightingId, reason, urgency, message, createdAtMicros, dismissed, owner | bridge (a dashboard dismissal is never undone by a stale upsert) |
| delete_alert | id | bridge (alert removed from SQLite) |
| upsert_person | id, name, description, relationship, knownSince, createdAtMicros, updatedAtMicros, owner | bridge |
| delete_person | id | bridge (person removed from SQLite) |
| dismiss_alert | id | anyone (dashboard; SpacetimeDB can't check RecallAI accounts, so the dashboard only offers it for the signed-in user's alerts) |

React accessor names (generated bindings, already in `frontend/live-dashboard/src/module_bindings/`):
`tables.belonging`, `tables.sighting`, `tables.alert`, `tables.person`; `reducers.dismissAlert`, etc.;
row types `Types.Belonging`, `Types.Sighting`, `Types.Alert`, `Types.Person` from `module_bindings/types`.
u64/i64 fields arrive in JS as **bigint** (`id`, `seenAtMicros`, `sightingId`, `createdAtMicros`, `updatedAtMicros`).
Regenerate them with the real CLI after any module change (never hand-edit):
`spacetime generate --lang typescript --out-dir ../frontend/live-dashboard/src/module_bindings --module-path .` (from `spacetimedb/`),
or `npm run spacetime:generate` in `frontend/live-dashboard/`.

## Existing FastAPI endpoints the dashboard may use (server.py, port 8000)
All of these need the signed-in user (`Authorization: Bearer <token>`, see [AUTH.md](AUTH.md))
and only return that user's data.
- `GET /snapshots/<file>` — images (paths as stored in imageUrl/cropUrl); only the owner of the
  sighting gets the file; `<img>` URLs carry `?token=<token>`
- `POST /api/ask` body `{"question": str, "history": [{"role","content"}], "timezone": IANA str}` →
  `{"answer": str, "sightings": [row dicts incl. id, crop_path, image_path, timestamp], "steps": [{tool,input}], "lookups"?: [...]}`
  (answer cites sightings as `[#id]`; 502 with `{"detail"}` if Grok fails)
- `GET /api/belongings/{id}/last-seen` — temporal summary (relative_time etc.)
- Also on the same server (used by `frontend/dashboard/`): `GET/POST/PUT/DELETE /api/people`,
  `GET /api/live`, `/api/live/frame.jpg`, `/api/live/stream` (camera mirror), `GET /api/enroll`,
  `POST /api/enroll/start|cancel` (camera enrollment).

## Two dashboards
- `frontend/dashboard/` (JSX, polls FastAPI): live camera, enrollment, people editor. Dev server `:5173`.
- `frontend/live-dashboard/` (TS, live SpacetimeDB subscription): belongings, sightings, alerts, people (read-only), Grok chat,
  plus a read-only live camera panel that polls `GET /api/live` (boxes, scores) and `/api/live/frame.jpg`.
  Dev server `:5174`; built copy served by FastAPI at `/app/`.
Both proxy `/api` and `/snapshots` to `:8000`; FastAPI's CORS allows both dev origins.

## Config (env vars, also readable from `.env` via llm.load_env_file)
- `SPACETIME_URL` (default `http://localhost:3000`)
- `SPACETIME_DB` (default `recallai`)
- Bridge token persisted in `.spacetime_token` (git-ignored)
- React: `VITE_SPACETIMEDB_HOST` (default `ws://<host that served the page>:3000`), `VITE_SPACETIMEDB_DB_NAME` (default `recallai`),
  `VITE_API_BASE` (default "" → same origin / Vite proxy to http://localhost:8000)

## Ownership (agents must only edit their own files)
- Module agent: `spacetimedb/**` (except `src/index.ts` contract changes need lead approval), `frontend/live-dashboard/src/module_bindings/**` (review only)
- Bridge agent: `backend/recallai/sync/spacetime_bridge.py`, `backend/tests/test_spacetime_bridge.py`, `backend/recallai/api/server.py`, `.gitignore`, `backend/requirements.txt`
- React agent: `frontend/live-dashboard/**` except `frontend/live-dashboard/src/module_bindings/**`
- Nobody else edits `agent.py`, `webcam.py`, `recorder.py`, `db.py`, `temporal_memory.py`, `llm.py`, existing tests.
