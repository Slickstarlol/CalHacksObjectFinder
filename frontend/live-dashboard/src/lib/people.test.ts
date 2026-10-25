import { describe, expect, it } from 'vitest';
import { knownFor, sortPeople } from './people';
import type { Person } from '../module_bindings/types';

const at = (y: number, m: number, d: number) => new Date(y, m - 1, d, 12);

describe('knownFor (matches people.known_for)', () => {
  it.each([
    ['2024-09-01', at(2026, 10, 24), '2 years and 1 month'],
    ['2025-10-24', at(2026, 10, 24), '1 year'],
    ['2026-08-25', at(2026, 10, 24), '1 month'],
    ['2026-10-01', at(2026, 10, 24), '23 days'],
    ['2026-10-23', at(2026, 10, 24), '1 day'],
    ['2026-10-24', at(2026, 10, 24), 'since today'],
    ['2024-01-31', at(2024, 3, 1), '1 month'],
  ])('%s on %s -> %s', (since, now, want) => {
    expect(knownFor(since, now)).toBe(want);
  });

  it('returns null for empty, invalid or future dates', () => {
    expect(knownFor('', at(2026, 10, 24))).toBeNull();
    expect(knownFor('soon', at(2026, 10, 24))).toBeNull();
    expect(knownFor('2026-02-30', at(2026, 10, 24))).toBeNull();
    expect(knownFor('2026-10-25', at(2026, 10, 24))).toBeNull();
  });
});

describe('sortPeople', () => {
  it('sorts by name case-insensitively, then id', () => {
    const p = (id: bigint, name: string): Person => ({
      id, name, description: '', relationship: '', knownSince: '',
      createdAtMicros: 0n, updatedAtMicros: 0n, owner: '1',
    });
    const out = sortPeople([p(3n, 'kent'), p(2n, 'Chawin'), p(1n, 'Kent')]);
    expect(out.map(x => x.id)).toEqual([2n, 1n, 3n]);
  });
});
