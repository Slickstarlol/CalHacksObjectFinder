// RecallAI SpacetimeDB module — the live, shared copy of RecallAI's memory.
//
// Python (webcam / Grok / watcher) still writes to SQLite first. The bridge
// (spacetime_bridge.py) mirrors that data here by calling the bridge-only
// reducers over HTTP, and the React dashboard subscribes to these tables, so
// every new sighting, Grok description and alert appears instantly.
//
// Column/argument keys are camelCase (TypeScript convention); SpacetimeDB
// stores them as snake_case columns (belongingId -> belonging_id), which is
// what SQL queries use. Reducer names stay snake_case.
//
// Reducer arguments are deliberately plain scalars (no Option, no Timestamp)
// so the Python bridge can send them as a simple JSON array over HTTP:
//   - times are microseconds since the Unix epoch (i64)
//   - "missing" text is ""        - missing GPS is hasGps = false
//   - verified: -1 unchecked, 0 Grok says not yours, 1 Grok confirmed

import { schema, t, table, SenderError } from 'spacetimedb/server';

/** Registered belonging (mirrors belongings/<id>/info.json). */
const belonging = table(
  { name: 'belonging', public: true },
  {
    id: t.string().primaryKey(),
    name: t.string(),
    category: t.string(),
    photos: t.u32(),
  }
);

/** One sighting of a belonging (mirrors a row of SQLite `sightings`). */
const sighting = table(
  { name: 'sighting', public: true },
  {
    id: t.u64().primaryKey(), // same id as SQLite
    belongingId: t.string().index('btree'),
    belongingName: t.string(),
    objectName: t.string(), // YOLO category
    event: t.string(), // 'seen' | 'left_view'
    seenAtMicros: t.i64(),
    imageUrl: t.string(), // e.g. "/snapshots/123_abc_seen.jpg" (served by FastAPI)
    cropUrl: t.string(), // "" if none
    description: t.string(), // Grok's location sentence, "" until interpreted
    place: t.string(), // Grok's short place label, "" until interpreted
    nearby: t.string(), // "laptop, bottle"
    matchScore: t.f64(), // DINOv2 similarity, 0 if unknown
    verified: t.i32(), // -1 unchecked, 0 not yours, 1 confirmed
    hasGps: t.bool(),
    latitude: t.f64(),
    longitude: t.f64(),
  }
);

/** "Did you forget X?" alert (mirrors SQLite `alerts`). */
const alert = table(
  { name: 'alert', public: true },
  {
    id: t.u64().primaryKey(), // same id as SQLite
    belongingId: t.string(),
    belongingName: t.string(),
    sightingId: t.u64(), // 0 if none
    reason: t.string(), // 'out_of_sight' | 'moved_away'
    urgency: t.string(), // 'low' | 'high' | 'none'
    message: t.string(),
    createdAtMicros: t.i64(),
    dismissed: t.bool(),
  }
);

/** The one identity allowed to write memory data (the Python bridge). Private. */
const bridge = table(
  { name: 'bridge' },
  {
    id: t.u32().primaryKey(), // always 0
    identity: t.identity(),
  }
);

const spacetimedb = schema({ belonging, sighting, alert, bridge });
export default spacetimedb;

type Ctx = Parameters<Parameters<typeof spacetimedb.reducer>[1]>[0];

function requireBridge(ctx: Ctx) {
  const row = ctx.db.bridge.id.find(0);
  if (!row || !row.identity.isEqual(ctx.sender)) {
    throw new SenderError('Only the RecallAI bridge can write memory data');
  }
}

/** First caller becomes the bridge; the same identity may call it again. */
export const register_bridge = spacetimedb.reducer(ctx => {
  const row = ctx.db.bridge.id.find(0);
  if (!row) {
    ctx.db.bridge.insert({ id: 0, identity: ctx.sender });
  } else if (!row.identity.isEqual(ctx.sender)) {
    throw new SenderError('A different bridge is already registered');
  }
});

export const upsert_belonging = spacetimedb.reducer(
  { id: t.string(), name: t.string(), category: t.string(), photos: t.u32() },
  (ctx, args) => {
    requireBridge(ctx);
    if (!args.id) throw new SenderError('belonging id must not be empty');
    if (ctx.db.belonging.id.find(args.id)) ctx.db.belonging.id.update(args);
    else ctx.db.belonging.insert(args);
  }
);

export const delete_belonging = spacetimedb.reducer(
  { id: t.string() },
  (ctx, { id }) => {
    requireBridge(ctx);
    ctx.db.belonging.id.delete(id);
  }
);

export const upsert_sighting = spacetimedb.reducer(
  {
    id: t.u64(),
    belongingId: t.string(),
    belongingName: t.string(),
    objectName: t.string(),
    event: t.string(),
    seenAtMicros: t.i64(),
    imageUrl: t.string(),
    cropUrl: t.string(),
    description: t.string(),
    place: t.string(),
    nearby: t.string(),
    matchScore: t.f64(),
    verified: t.i32(),
    hasGps: t.bool(),
    latitude: t.f64(),
    longitude: t.f64(),
  },
  (ctx, args) => {
    requireBridge(ctx);
    if (args.event !== 'seen' && args.event !== 'left_view') {
      throw new SenderError(`unknown event ${args.event}`);
    }
    if (ctx.db.sighting.id.find(args.id)) ctx.db.sighting.id.update(args);
    else ctx.db.sighting.insert(args);
  }
);

export const delete_sighting = spacetimedb.reducer(
  { id: t.u64() },
  (ctx, { id }) => {
    requireBridge(ctx);
    ctx.db.sighting.id.delete(id);
  }
);

export const upsert_alert = spacetimedb.reducer(
  {
    id: t.u64(),
    belongingId: t.string(),
    belongingName: t.string(),
    sightingId: t.u64(),
    reason: t.string(),
    urgency: t.string(),
    message: t.string(),
    createdAtMicros: t.i64(),
    dismissed: t.bool(),
  },
  (ctx, args) => {
    requireBridge(ctx);
    const existing = ctx.db.alert.id.find(args.id);
    if (existing) {
      // A dismissal made in the dashboard wins over a stale bridge copy.
      ctx.db.alert.id.update({
        ...args,
        dismissed: args.dismissed || existing.dismissed,
      });
    } else {
      ctx.db.alert.insert(args);
    }
  }
);

export const delete_alert = spacetimedb.reducer(
  { id: t.u64() },
  (ctx, { id }) => {
    requireBridge(ctx);
    ctx.db.alert.id.delete(id);
  }
);

/** Called from the React dashboard by anyone viewing it. */
export const dismiss_alert = spacetimedb.reducer(
  { id: t.u64() },
  (ctx, { id }) => {
    const row = ctx.db.alert.id.find(id);
    if (!row) throw new SenderError(`no alert ${id}`);
    if (!row.dismissed) ctx.db.alert.id.update({ ...row, dismissed: true });
  }
);
