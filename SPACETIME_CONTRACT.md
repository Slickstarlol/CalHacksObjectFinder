# RecallAI × SpacetimeDB — integration contract

Read this first. Every component (module, Python bridge, React dashboard) must
match it exactly. The SpacetimeDB **module source is the source of truth**:
`spacetime/src/index.ts` (already written and smoke-tested — do not change
table/column/reducer names or argument order without telling the lead).

## Architecture

```
webcam.py ─▶ SQLite memory.db (unchanged, still the primary store)
                 │
                 ▼  spacetime_bridge.py (background thread inside server.py)
          HTTP: POST {SPACETIME_URL}/v1/database/{DB}/call/{reducer}  (JSON array args, Bearer token)
                 ▼
        SpacetimeDB database "recallai"  (tables: belonging, sighting, alert)
                 │  live WebSocket subscription (spacetimedb/react)
                 ▼
        React dashboard (web/, Vite + React + TS)
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
| id | id | string | `belongings/<id>/info.json` id |
| name | name | string | info.json name |
| category | category | string | info.json category |
| photos | photos | u32 | number of `reference_*` images |

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

Only alerts with `dismissed = 0` in SQLite need to be pushed initially, but pushing all is fine (idempotent).

## Reducers

HTTP calls send a **JSON array of arguments in exactly this order**. Python ints are fine for u64/i64/u32/i32.

| reducer | args (in order) | who may call |
|---|---|---|
| register_bridge | (none → body `[]`) | first caller becomes the bridge; same identity may repeat |
| upsert_belonging | id, name, category, photos | bridge |
| delete_belonging | id | bridge |
| upsert_sighting | id, belongingId, belongingName, objectName, event, seenAtMicros, imageUrl, cropUrl, description, place, nearby, matchScore, verified, hasGps, latitude, longitude | bridge |
| delete_sighting | id | bridge |
| upsert_alert | id, belongingId, belongingName, sightingId, reason, urgency, message, createdAtMicros, dismissed | bridge (a dashboard dismissal is never undone by a stale upsert) |
| delete_alert | id | bridge (alert removed from SQLite) |
| dismiss_alert | id | anyone (dashboard) |

React accessor names (generated bindings, already in `web/src/module_bindings/`):
`tables.belonging`, `tables.sighting`, `tables.alert`; `reducers.dismissAlert`, etc.;
row types `Types.Belonging`, `Types.Sighting`, `Types.Alert` from `module_bindings/types`.
u64/i64 fields arrive in JS as **bigint** (`id`, `seenAtMicros`, `sightingId`, `createdAtMicros`).

## Existing FastAPI endpoints the dashboard may use (server.py, port 8000)
- `GET /snapshots/<file>` — images (paths as stored in imageUrl/cropUrl)
- `POST /api/ask` body `{"question": str, "history": [{"role","content"}], "timezone": IANA str}` →
  `{"answer": str, "sightings": [row dicts incl. id, crop_path, image_path, timestamp], "steps": [{tool,input}], "lookups"?: [...]}`
  (answer cites sightings as `[#id]`; 502 with `{"detail"}` if Grok fails)
- `GET /api/belongings/{id}/last-seen` — temporal summary (relative_time etc.)

## Config (env vars, also readable from `.env` via llm.load_env_file)
- `SPACETIME_URL` (default `http://localhost:3000`)
- `SPACETIME_DB` (default `recallai`)
- Bridge token persisted in `.spacetime_token` (git-ignored)
- React: `VITE_SPACETIMEDB_HOST` (default `ws://<host that served the page>:3000`), `VITE_SPACETIMEDB_DB_NAME` (default `recallai`),
  `VITE_API_BASE` (default "" → same origin / Vite proxy to http://localhost:8000)

## Ownership (agents must only edit their own files)
- Module agent: `spacetime/**` (except `src/index.ts` contract changes need lead approval), `web/src/module_bindings/**` (review only)
- Bridge agent: `spacetime_bridge.py`, `tests/test_spacetime_bridge.py`, `server.py`, `.gitignore`, `requirements.txt`
- React agent: `web/**` except `web/src/module_bindings/**`
- Nobody else edits `agent.py`, `webcam.py`, `recorder.py`, `db.py`, `temporal_memory.py`, `llm.py`, existing tests.
