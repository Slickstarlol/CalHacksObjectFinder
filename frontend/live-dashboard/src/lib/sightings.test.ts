import { describe, expect, it } from 'vitest';
import { makeSighting as s } from '../demo/fixtures';
import type { Alert } from '../module_bindings/types';
import {
  activeAlerts,
  frameUrl,
  groupVisibleByBelonging,
  indexById,
  latestVisibleByBelonging,
  sortBelongings,
  splitNearby,
  thumbUrl,
  timelineFor,
} from './sightings';

const rows = [
  s({ id: 1n, belongingId: 'a', seenAtMicros: 100n }),
  s({ id: 2n, belongingId: 'a', seenAtMicros: 300n, verified: 0 }), // hidden: Grok said not yours
  s({ id: 3n, belongingId: 'a', seenAtMicros: 200n, verified: -1 }),
  s({ id: 4n, belongingId: 'b', seenAtMicros: 50n }),
  s({ id: 5n, belongingId: 'b', seenAtMicros: 50n }), // tie -> higher id wins
  s({ id: 6n, belongingId: '', seenAtMicros: 999n }),
  s({ id: 7n, belongingId: 'c', seenAtMicros: 10n, verified: 0 }),
];

describe('latestVisibleByBelonging', () => {
  it('picks the newest non-rejected sighting per belonging', () => {
    const m = latestVisibleByBelonging(rows);
    expect(m.get('a')?.id).toBe(3n);
    expect(m.get('b')?.id).toBe(5n);
    expect(m.has('c')).toBe(false); // only rejected rows
    expect(m.has('')).toBe(false);
  });
});

describe('groupVisibleByBelonging / timelineFor', () => {
  it('groups visible sightings newest first', () => {
    const g = groupVisibleByBelonging(rows);
    expect(g.get('a')!.map(r => r.id)).toEqual([3n, 1n]);
    expect(g.get('b')!.map(r => r.id)).toEqual([5n, 4n]);
  });
  it('caps the timeline', () => {
    const many = Array.from({ length: 80 }, (_, i) =>
      s({ id: BigInt(i), belongingId: 'x', seenAtMicros: BigInt(i) })
    );
    const t = timelineFor(many, 'x');
    expect(t).toHaveLength(50);
    expect(t[0].id).toBe(79n);
    expect(timelineFor(rows, 'a', 1).map(r => r.id)).toEqual([3n]);
  });
});

describe('sortBelongings', () => {
  it('orders by most recently seen, never-seen last alphabetically', () => {
    const bs = [
      { id: 'z', name: 'Zeta', category: '', photos: 0, tracking: true },
      { id: 'b', name: 'B', category: '', photos: 0, tracking: true },
      { id: 'y', name: 'Alpha', category: '', photos: 0, tracking: true },
      { id: 'a', name: 'A', category: '', photos: 0, tracking: true },
    ];
    expect(sortBelongings(bs, latestVisibleByBelonging(rows)).map(b => b.id)).toEqual(['a', 'b', 'y', 'z']);
  });
});

describe('activeAlerts', () => {
  const al = (id: bigint, at: bigint, dismissed = false): Alert => ({
    id, belongingId: 'a', belongingName: 'A', sightingId: 0n, reason: '', urgency: 'low',
    message: '', createdAtMicros: at, dismissed,
  });
  it('drops dismissed and sorts newest first', () => {
    expect(activeAlerts([al(1n, 10n), al(2n, 30n, true), al(3n, 20n), al(4n, 20n)]).map(a => a.id)).toEqual([4n, 3n, 1n]);
  });
});

describe('urls and helpers', () => {
  it('prefers crop for thumbs and frame for full view', () => {
    const r = { cropUrl: '/snapshots/crops/c.jpg', imageUrl: '/snapshots/f.jpg' };
    expect(thumbUrl(r)).toBe('/snapshots/crops/c.jpg');
    expect(frameUrl(r)).toBe('/snapshots/f.jpg');
    expect(thumbUrl({ cropUrl: '', imageUrl: '/snapshots/f.jpg' }, 'http://h:8000/')).toBe('http://h:8000/snapshots/f.jpg');
    expect(thumbUrl({ cropUrl: '', imageUrl: '' })).toBe('');
  });
  it('splits nearby lists', () => {
    expect(splitNearby(' laptop, bottle,,Laptop , ')).toEqual(['laptop', 'bottle']);
    expect(splitNearby('')).toEqual([]);
  });
  it('indexes by string id', () => {
    expect(indexById(rows).get('3')?.id).toBe(3n);
  });
});
