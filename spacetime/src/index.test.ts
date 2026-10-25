import { describe, expect, it } from 'vitest';
import { ConnectionId, type Identity } from 'spacetimedb';
import { SenderError } from 'spacetimedb/server';
import {
  createModuleTestHarness,
  TestAuth,
} from 'spacetimedb/server/test-utils';
import spacetime, * as m from './index';

const auth = (sub: string, c: bigint) =>
  TestAuth.fromJwtPayload(
    JSON.stringify({ iss: 'recallai-test', sub }),
    new ConnectionId(c)
  );

const BRIDGE = auth('bridge', 1n);
const BROWSER = auth('browser', 2n);
const STRANGER = auth('stranger', 3n);

type SightingArgs = Parameters<typeof m.upsert_sighting>[1];
type AlertArgs = Parameters<typeof m.upsert_alert>[1];
type BelongingArgs = Parameters<typeof m.upsert_belonging>[1];

const U64_MAX = (1n << 64n) - 1n;
const I64_MAX = (1n << 63n) - 1n;
const I64_MIN = -(1n << 63n);

function belongingArgs(over: Partial<BelongingArgs> = {}): BelongingArgs {
  return { id: 'mouse1', name: 'My Mouse', category: 'mouse', photos: 3, ...over };
}

function sightingArgs(over: Partial<SightingArgs> = {}): SightingArgs {
  return {
    id: 7n,
    belongingId: 'mouse1',
    belongingName: 'My Mouse',
    objectName: 'mouse',
    event: 'seen',
    seenAtMicros: 1792860000123456n,
    imageUrl: '/snapshots/7_abc_seen.jpg',
    cropUrl: '/snapshots/7_abc_crop.jpg',
    description: 'On the desk next to the laptop.',
    place: 'desk',
    nearby: 'laptop, bottle',
    matchScore: 0.8734512345678901,
    verified: 1,
    hasGps: true,
    latitude: 33.640495,
    longitude: -117.844296,
    ...over,
  };
}

function alertArgs(over: Partial<AlertArgs> = {}): AlertArgs {
  return {
    id: 1n,
    belongingId: 'mouse1',
    belongingName: 'My Mouse',
    sightingId: 7n,
    reason: 'out_of_sight',
    urgency: 'low',
    message: 'Did you forget your mouse?',
    createdAtMicros: 1792860000000000n,
    dismissed: false,
    ...over,
  };
}

/** Fresh harness with BRIDGE already registered. */
function setup() {
  const h = createModuleTestHarness(spacetime, m);
  h.withReducerTx(BRIDGE, ctx => m.register_bridge(ctx, {}));
  return h;
}

type H = ReturnType<typeof setup>;
const counts = (h: H) => ({
  belonging: Number(h.db.belonging.count()),
  sighting: Number(h.db.sighting.count()),
  alert: Number(h.db.alert.count()),
});

/** Runs a reducer call expecting a SenderError with a matching message. */
function expectSenderError(fn: () => unknown, msg: RegExp) {
  let err: unknown;
  try {
    fn();
  } catch (e) {
    err = e;
  }
  expect(err, 'expected the reducer to throw').toBeDefined();
  expect(err).toBeInstanceOf(SenderError);
  expect(String((err as Error).message)).toMatch(msg);
}

describe('register_bridge', () => {
  it('first caller becomes the bridge', () => {
    const h = createModuleTestHarness(spacetime, m);
    let sender: Identity | undefined;
    h.withReducerTx(BRIDGE, ctx => {
      sender = ctx.sender;
      m.register_bridge(ctx, {});
    });
    const row = h.db.bridge.id.find(0);
    expect(row).not.toBeNull();
    expect(row!.identity.isEqual(sender!)).toBe(true);
    expect(Number(h.db.bridge.count())).toBe(1);
  });

  it('same identity may register again (idempotent)', () => {
    const h = setup();
    expect(() => h.withReducerTx(BRIDGE, ctx => m.register_bridge(ctx, {}))).not.toThrow();
    // A new connection id with the same JWT subject is the same identity.
    const bridgeReconnect = auth('bridge', 99n);
    expect(() =>
      h.withReducerTx(bridgeReconnect, ctx => m.register_bridge(ctx, {}))
    ).not.toThrow();
    expect(Number(h.db.bridge.count())).toBe(1);
    h.withReducerTx(bridgeReconnect, ctx => m.upsert_belonging(ctx, belongingArgs()));
    expect(h.db.belonging.id.find('mouse1')).not.toBeNull();
  });

  it('a different identity is rejected and the bridge is unchanged', () => {
    const h = setup();
    let bridgeId: Identity | undefined;
    h.withReducerTx(BRIDGE, ctx => (bridgeId = ctx.sender));
    expectSenderError(
      () => h.withReducerTx(STRANGER, ctx => m.register_bridge(ctx, {})),
      /different bridge/
    );
    expect(h.db.bridge.id.find(0)!.identity.isEqual(bridgeId!)).toBe(true);
    expect(Number(h.db.bridge.count())).toBe(1);
  });

  it('writes are rejected before any bridge is registered', () => {
    const h = createModuleTestHarness(spacetime, m);
    expectSenderError(
      () => h.withReducerTx(BRIDGE, ctx => m.upsert_belonging(ctx, belongingArgs())),
      /bridge/
    );
    expectSenderError(
      () => h.withReducerTx(BRIDGE, ctx => m.upsert_sighting(ctx, sightingArgs())),
      /bridge/
    );
    expectSenderError(
      () => h.withReducerTx(BRIDGE, ctx => m.upsert_alert(ctx, alertArgs())),
      /bridge/
    );
    expectSenderError(
      () => h.withReducerTx(BRIDGE, ctx => m.delete_belonging(ctx, { id: 'x' })),
      /bridge/
    );
    expectSenderError(
      () => h.withReducerTx(BRIDGE, ctx => m.delete_sighting(ctx, { id: 1n })),
      /bridge/
    );
    expect(counts(h)).toEqual({ belonging: 0, sighting: 0, alert: 0 });
    expect(Number(h.db.bridge.count())).toBe(0);
  });
});

describe('upsert_belonging / delete_belonging', () => {
  it('inserts then updates the same id without duplicating', () => {
    const h = setup();
    h.withReducerTx(BRIDGE, ctx => m.upsert_belonging(ctx, belongingArgs()));
    expect(h.db.belonging.id.find('mouse1')).toEqual(belongingArgs());
    h.withReducerTx(BRIDGE, ctx =>
      m.upsert_belonging(ctx, belongingArgs({ name: 'Logitech Mouse', photos: 5 }))
    );
    expect(h.db.belonging.id.find('mouse1')).toEqual(
      belongingArgs({ name: 'Logitech Mouse', photos: 5 })
    );
    expect(counts(h).belonging).toBe(1);
    h.withReducerTx(BRIDGE, ctx => m.upsert_belonging(ctx, belongingArgs({ id: 'keys1' })));
    expect(counts(h).belonging).toBe(2);
  });

  it('rejects an empty id', () => {
    const h = setup();
    expectSenderError(
      () => h.withReducerTx(BRIDGE, ctx => m.upsert_belonging(ctx, belongingArgs({ id: '' }))),
      /must not be empty/
    );
    expect(counts(h).belonging).toBe(0);
  });

  it('rejects non-bridge callers and leaves state unchanged', () => {
    const h = setup();
    h.withReducerTx(BRIDGE, ctx => m.upsert_belonging(ctx, belongingArgs()));
    for (const who of [BROWSER, STRANGER]) {
      expectSenderError(
        () => h.withReducerTx(who, ctx =>
          m.upsert_belonging(ctx, belongingArgs({ name: 'HACKED' }))),
        /bridge/
      );
      expectSenderError(
        () => h.withReducerTx(who, ctx => m.upsert_belonging(ctx, belongingArgs({ id: 'new' }))),
        /bridge/
      );
      expectSenderError(
        () => h.withReducerTx(who, ctx => m.delete_belonging(ctx, { id: 'mouse1' })),
        /bridge/
      );
    }
    expect(h.db.belonging.id.find('mouse1')).toEqual(belongingArgs());
    expect(counts(h).belonging).toBe(1);
  });

  it('bridge deletes; deleting a missing id is a silent no-op', () => {
    const h = setup();
    h.withReducerTx(BRIDGE, ctx => m.upsert_belonging(ctx, belongingArgs()));
    h.withReducerTx(BRIDGE, ctx => m.delete_belonging(ctx, { id: 'mouse1' }));
    expect(h.db.belonging.id.find('mouse1')).toBeNull();
    expect(() =>
      h.withReducerTx(BRIDGE, ctx => m.delete_belonging(ctx, { id: 'mouse1' }))
    ).not.toThrow();
    expect(() =>
      h.withReducerTx(BRIDGE, ctx => m.delete_belonging(ctx, { id: 'never-existed' }))
    ).not.toThrow();
    expect(counts(h).belonging).toBe(0);
  });
});

describe('upsert_sighting / delete_sighting', () => {
  it('round-trips all 16 fields exactly', () => {
    const h = setup();
    const cases: SightingArgs[] = [
      sightingArgs(),
      sightingArgs({
        id: U64_MAX,
        event: 'left_view',
        seenAtMicros: I64_MAX,
        cropUrl: '',
        description: '',
        place: '',
        nearby: '',
        matchScore: 0,
        verified: -1,
        hasGps: false,
        latitude: 0,
        longitude: 0,
      }),
      sightingArgs({
        id: 0n,
        seenAtMicros: I64_MIN,
        verified: 0,
        matchScore: 0.1 + 0.2,
        latitude: -90,
        longitude: 180,
        description: 'Ünïcode ✓ — "quotes", newline\nok',
      }),
    ];
    for (const s of cases) {
      h.withReducerTx(BRIDGE, ctx => m.upsert_sighting(ctx, s));
    }
    for (const s of cases) {
      const row = h.db.sighting.id.find(s.id);
      expect(row).toEqual(s);
      expect(Object.keys(row!)).toHaveLength(16);
      expect(typeof row!.id).toBe('bigint');
      expect(typeof row!.seenAtMicros).toBe('bigint');
      expect(typeof row!.matchScore).toBe('number');
      expect(typeof row!.verified).toBe('number');
      expect(typeof row!.hasGps).toBe('boolean');
    }
    expect(h.db.sighting.id.find(0n)!.matchScore).toBe(0.30000000000000004);
    expect(counts(h).sighting).toBe(3);
  });

  it('inserts then updates the same id without duplicating', () => {
    const h = setup();
    h.withReducerTx(BRIDGE, ctx =>
      m.upsert_sighting(ctx, sightingArgs({ description: '', place: '', verified: -1 }))
    );
    // Grok interprets later; the bridge re-sends the full row.
    const updated = sightingArgs({ description: 'Under the monitor.', place: 'desk', verified: 1 });
    h.withReducerTx(BRIDGE, ctx => m.upsert_sighting(ctx, updated));
    expect(h.db.sighting.id.find(7n)).toEqual(updated);
    expect(counts(h).sighting).toBe(1);
  });

  it('rejects an invalid event and leaves state unchanged', () => {
    const h = setup();
    h.withReducerTx(BRIDGE, ctx => m.upsert_sighting(ctx, sightingArgs()));
    for (const event of ['', 'SEEN', 'moved', 'left']) {
      expectSenderError(
        () => h.withReducerTx(BRIDGE, ctx => m.upsert_sighting(ctx, sightingArgs({ event }))),
        /unknown event/
      );
      expectSenderError(
        () => h.withReducerTx(BRIDGE, ctx =>
          m.upsert_sighting(ctx, sightingArgs({ id: 8n, event }))),
        /unknown event/
      );
    }
    expect(h.db.sighting.id.find(7n)).toEqual(sightingArgs());
    expect(h.db.sighting.id.find(8n)).toBeNull();
  });

  it('rejects non-bridge callers and leaves state unchanged', () => {
    const h = setup();
    h.withReducerTx(BRIDGE, ctx => m.upsert_sighting(ctx, sightingArgs()));
    for (const who of [BROWSER, STRANGER]) {
      expectSenderError(
        () => h.withReducerTx(who, ctx => m.upsert_sighting(ctx, sightingArgs({ place: 'x' }))),
        /bridge/
      );
      expectSenderError(
        () => h.withReducerTx(who, ctx => m.upsert_sighting(ctx, sightingArgs({ id: 9n }))),
        /bridge/
      );
      expectSenderError(
        () => h.withReducerTx(who, ctx => m.delete_sighting(ctx, { id: 7n })),
        /bridge/
      );
    }
    expect(h.db.sighting.id.find(7n)).toEqual(sightingArgs());
    expect(counts(h).sighting).toBe(1);
  });

  it('bridge deletes; deleting a missing id is a silent no-op', () => {
    const h = setup();
    h.withReducerTx(BRIDGE, ctx => m.upsert_sighting(ctx, sightingArgs()));
    h.withReducerTx(BRIDGE, ctx => m.delete_sighting(ctx, { id: 7n }));
    expect(h.db.sighting.id.find(7n)).toBeNull();
    expect(() => h.withReducerTx(BRIDGE, ctx => m.delete_sighting(ctx, { id: 7n }))).not.toThrow();
    expect(() =>
      h.withReducerTx(BRIDGE, ctx => m.delete_sighting(ctx, { id: 12345n }))
    ).not.toThrow();
    expect(counts(h).sighting).toBe(0);
  });

  it('btree index on belongingId filters correctly and tracks updates/deletes', () => {
    const h = setup();
    h.withReducerTx(BRIDGE, ctx => {
      m.upsert_sighting(ctx, sightingArgs({ id: 1n, belongingId: 'mouse1' }));
      m.upsert_sighting(ctx, sightingArgs({ id: 2n, belongingId: 'mouse1', event: 'left_view' }));
      m.upsert_sighting(ctx, sightingArgs({ id: 3n, belongingId: 'keys1', belongingName: 'Keys' }));
    });
    const ids = (b: string) =>
      [...h.db.sighting.belongingId.filter(b)].map(r => r.id).sort();
    expect(ids('mouse1')).toEqual([1n, 2n]);
    expect(ids('keys1')).toEqual([3n]);
    expect(ids('nope')).toEqual([]);

    // Re-assigning a sighting moves it between index buckets.
    h.withReducerTx(BRIDGE, ctx =>
      m.upsert_sighting(ctx, sightingArgs({ id: 2n, belongingId: 'keys1' }))
    );
    expect(ids('mouse1')).toEqual([1n]);
    expect(ids('keys1')).toEqual([2n, 3n]);

    h.withReducerTx(BRIDGE, ctx => m.delete_sighting(ctx, { id: 3n }));
    expect(ids('keys1')).toEqual([2n]);
  });
});

describe('upsert_alert', () => {
  it('inserts then updates the same id without duplicating', () => {
    const h = setup();
    h.withReducerTx(BRIDGE, ctx => m.upsert_alert(ctx, alertArgs()));
    expect(h.db.alert.id.find(1n)).toEqual(alertArgs());
    const updated = alertArgs({
      urgency: 'high',
      reason: 'moved_away',
      message: 'Your mouse moved away!',
      sightingId: 0n,
      createdAtMicros: I64_MAX,
    });
    h.withReducerTx(BRIDGE, ctx => m.upsert_alert(ctx, updated));
    expect(h.db.alert.id.find(1n)).toEqual(updated);
    expect(counts(h).alert).toBe(1);
  });

  it('round-trips extreme u64 ids', () => {
    const h = setup();
    const a = alertArgs({ id: U64_MAX, sightingId: U64_MAX, createdAtMicros: I64_MIN });
    h.withReducerTx(BRIDGE, ctx => m.upsert_alert(ctx, a));
    expect(h.db.alert.id.find(U64_MAX)).toEqual(a);
  });

  it('bridge can insert an already-dismissed alert and dismiss via upsert', () => {
    const h = setup();
    h.withReducerTx(BRIDGE, ctx => m.upsert_alert(ctx, alertArgs({ id: 2n, dismissed: true })));
    expect(h.db.alert.id.find(2n)!.dismissed).toBe(true);
    h.withReducerTx(BRIDGE, ctx => m.upsert_alert(ctx, alertArgs()));
    h.withReducerTx(BRIDGE, ctx => m.upsert_alert(ctx, alertArgs({ dismissed: true })));
    expect(h.db.alert.id.find(1n)!.dismissed).toBe(true);
  });

  it('rejects non-bridge callers and leaves state unchanged', () => {
    const h = setup();
    h.withReducerTx(BRIDGE, ctx => m.upsert_alert(ctx, alertArgs()));
    for (const who of [BROWSER, STRANGER]) {
      expectSenderError(
        () => h.withReducerTx(who, ctx => m.upsert_alert(ctx, alertArgs({ message: 'spam' }))),
        /bridge/
      );
      expectSenderError(
        () => h.withReducerTx(who, ctx => m.upsert_alert(ctx, alertArgs({ id: 5n }))),
        /bridge/
      );
    }
    expect(h.db.alert.id.find(1n)).toEqual(alertArgs());
    expect(counts(h).alert).toBe(1);
  });
});

describe('dismiss_alert', () => {
  it('anyone (browser, stranger, bridge) may dismiss', () => {
    const h = setup();
    h.withReducerTx(BRIDGE, ctx => {
      m.upsert_alert(ctx, alertArgs({ id: 1n }));
      m.upsert_alert(ctx, alertArgs({ id: 2n }));
      m.upsert_alert(ctx, alertArgs({ id: 3n }));
    });
    h.withReducerTx(BROWSER, ctx => m.dismiss_alert(ctx, { id: 1n }));
    h.withReducerTx(STRANGER, ctx => m.dismiss_alert(ctx, { id: 2n }));
    h.withReducerTx(BRIDGE, ctx => m.dismiss_alert(ctx, { id: 3n }));
    for (const id of [1n, 2n, 3n]) {
      // Only `dismissed` changes.
      expect(h.db.alert.id.find(id)).toEqual(alertArgs({ id, dismissed: true }));
    }
  });

  it('unknown id throws even when no bridge is registered', () => {
    const h = createModuleTestHarness(spacetime, m);
    expectSenderError(
      () => h.withReducerTx(BROWSER, ctx => m.dismiss_alert(ctx, { id: 1n })),
      /no alert 1/
    );
  });

  it('is idempotent', () => {
    const h = setup();
    h.withReducerTx(BRIDGE, ctx => m.upsert_alert(ctx, alertArgs()));
    h.withReducerTx(BROWSER, ctx => m.dismiss_alert(ctx, { id: 1n }));
    expect(() => h.withReducerTx(BROWSER, ctx => m.dismiss_alert(ctx, { id: 1n }))).not.toThrow();
    expect(() => h.withReducerTx(STRANGER, ctx => m.dismiss_alert(ctx, { id: 1n }))).not.toThrow();
    expect(h.db.alert.id.find(1n)).toEqual(alertArgs({ dismissed: true }));
    expect(counts(h).alert).toBe(1);
  });

  it('unknown id throws SenderError and creates nothing', () => {
    const h = setup();
    expectSenderError(
      () => h.withReducerTx(BROWSER, ctx => m.dismiss_alert(ctx, { id: 404n })),
      /no alert 404/
    );
    expect(counts(h).alert).toBe(0);
  });

  it('a later bridge upsert with dismissed=false does not un-dismiss, but other fields update', () => {
    const h = setup();
    h.withReducerTx(BRIDGE, ctx => m.upsert_alert(ctx, alertArgs()));
    h.withReducerTx(BROWSER, ctx => m.dismiss_alert(ctx, { id: 1n }));
    const stale = alertArgs({ dismissed: false, urgency: 'high', message: 'still here?' });
    h.withReducerTx(BRIDGE, ctx => m.upsert_alert(ctx, stale));
    expect(h.db.alert.id.find(1n)).toEqual({ ...stale, dismissed: true });
    expect(counts(h).alert).toBe(1);
  });
});

describe('transactions', () => {
  it('a failing call inside a tx rolls back earlier writes in the same tx', () => {
    const h = setup();
    expect(() =>
      h.withReducerTx(BRIDGE, ctx => {
        m.upsert_belonging(ctx, belongingArgs());
        m.upsert_sighting(ctx, sightingArgs({ event: 'bogus' }));
      })
    ).toThrow(/unknown event/);
    expect(counts(h)).toEqual({ belonging: 0, sighting: 0, alert: 0 });
  });
});

describe('contract smoke', () => {
  it('bridge writes, browser dismisses, strangers rejected', () => {
    const h = createModuleTestHarness(spacetime, m);
    h.withReducerTx(BRIDGE, ctx => m.register_bridge(ctx, {}));
    h.withReducerTx(BRIDGE, ctx =>
      m.upsert_sighting(ctx, sightingArgs({ event: 'left_view', verified: -1 }))
    );
    expect(h.db.sighting.id.find(7n)?.belongingName).toBe('My Mouse');
    expect(() =>
      h.withReducerTx(BROWSER, ctx =>
        m.upsert_belonging(ctx, { id: 'x', name: 'X', category: 'mouse', photos: 1 })
      )
    ).toThrow(/bridge/);
    h.withReducerTx(BRIDGE, ctx => m.upsert_alert(ctx, alertArgs()));
    h.withReducerTx(BROWSER, ctx => m.dismiss_alert(ctx, { id: 1n }));
    expect(h.db.alert.id.find(1n)?.dismissed).toBe(true);
  });
});

describe('delete_alert (added after review)', () => {
  it('bridge can delete alerts; others cannot; missing id is a no-op', () => {
    const h = createModuleTestHarness(spacetime, m);
    const b = TestAuth.fromJwtPayload(JSON.stringify({ iss: 't', sub: 'bridge' }), new ConnectionId(91n));
    const s = TestAuth.fromJwtPayload(JSON.stringify({ iss: 't', sub: 'stranger' }), new ConnectionId(92n));
    h.withReducerTx(b, ctx => m.register_bridge(ctx, {}));
    h.withReducerTx(b, ctx => m.upsert_alert(ctx, { id: 5n, belongingId: 'm', belongingName: 'M',
      sightingId: 0n, reason: 'out_of_sight', urgency: 'low', message: 'x', createdAtMicros: 1n, dismissed: false }));
    expect(() => h.withReducerTx(s, ctx => m.delete_alert(ctx, { id: 5n }))).toThrow(/bridge/);
    expect(h.db.alert.id.find(5n)).not.toBeNull();
    h.withReducerTx(b, ctx => m.delete_alert(ctx, { id: 5n }));
    expect(h.db.alert.id.find(5n)).toBeNull();
    h.withReducerTx(b, ctx => m.delete_alert(ctx, { id: 5n }));
  });
});
