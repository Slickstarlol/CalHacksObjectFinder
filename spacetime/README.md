# RecallAI SpacetimeDB module

The live, shared copy of RecallAI's memory. Python still writes to SQLite first;
`spacetime_bridge.py` mirrors belongings, sightings and alerts here through the
bridge-only reducers, and the React dashboard (`../web`) subscribes over WebSocket
so new sightings and alerts appear instantly. The spec is
[`../SPACETIME_CONTRACT.md`](../SPACETIME_CONTRACT.md).

- Tables (public): `belonging`, `sighting` (btree index on `belonging_id`), `alert`. Private: `bridge`.
- Reducers: `register_bridge` (first caller becomes the bridge), `upsert_belonging`,
  `delete_belonging`, `upsert_sighting`, `delete_sighting`, `upsert_alert` (bridge only),
  `dismiss_alert` (anyone; a later bridge upsert never un-dismisses).

## Test (no server needed)

```bash
npm install
npx tsc --noEmit -p .
npx vitest run
```

## Run locally on Windows (PowerShell)

```powershell
# 1. Install the CLI (once), then open a new terminal
iwr https://windows.spacetimedb.com -useb | iex

# 2. Start a local server (http://localhost:3000); leave this window open
spacetime start

# 3. In another terminal, from this `spacetime` folder: build + publish as "recallai"
cd spacetime
npm install
spacetime publish --server local recallai
```

Useful afterwards:

```powershell
spacetime logs --server local recallai                     # module logs (-f to follow)
spacetime sql  --server local recallai "SELECT * FROM sighting"
```

## Regenerate the dashboard bindings

Run after any change to `src/index.ts` (from this folder):

```powershell
spacetime generate --lang typescript --out-dir ../web/src/module_bindings --module-path .
```

## Clear all data

Republish while wiping the database (also needed after a breaking schema change).
This also wipes the private `bridge` row, so the bridge must call `register_bridge`
again before it can write (the first identity to call it becomes the bridge).

```powershell
spacetime publish --server local --delete-data=always recallai
```

Use `--delete-data=on-conflict` to wipe only when the schema change is breaking;
add `-y` to skip the confirmation prompt.
