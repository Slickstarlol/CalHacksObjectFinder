import { describe, expect, it } from 'vitest';
import { browserTimeZone, formatClock, formatRelative, microsToDate, microsToMillis } from './time';

const now = new Date('2026-10-24T15:00:00Z');
const ago = (ms: number) => new Date(now.getTime() - ms);
const MIN = 60_000;

describe('microsToDate', () => {
  it('converts bigint microseconds to a Date', () => {
    expect(microsToDate(1_792_851_254_577_123n).getTime()).toBe(1_792_851_254_577);
    expect(microsToMillis(5_000n)).toBe(5);
    expect(microsToDate(0n).toISOString()).toBe('1970-01-01T00:00:00.000Z');
  });
});

describe('formatRelative', () => {
  it.each([
    [10_000, 'just now'],
    [-60_000, 'just now'], // future (clock skew)
    [50_000, '1 minute ago'],
    [23 * MIN, '23 minutes ago'],
    [59 * MIN, '59 minutes ago'],
    [60 * MIN, '1 hour ago'],
    [5 * 60 * MIN + 59 * MIN, '5 hours ago'],
    [30 * 60 * MIN, 'yesterday'],
    [3 * 24 * 60 * MIN, '3 days ago'],
  ])('%i ms ago -> %s', (ms, expected) => {
    expect(formatRelative(ago(ms), now)).toBe(expected);
  });

  it('falls back to a date for old timestamps', () => {
    const s = formatRelative(new Date('2026-09-01T12:00:00Z'), now);
    expect(s).not.toMatch(/ago/);
    expect(s).toMatch(/Sep|9/);
  });
});

describe('formatClock', () => {
  it('omits the date for today and includes it otherwise', () => {
    const local = new Date(2026, 9, 24, 14, 5);
    const today = new Date(2026, 9, 24, 18, 0);
    expect(formatClock(local, today)).not.toMatch(/Oct/);
    expect(formatClock(new Date(2026, 9, 22, 14, 5), today)).toMatch(/Oct/);
  });
});

describe('browserTimeZone', () => {
  it('returns a non-empty IANA name', () => {
    expect(browserTimeZone().length).toBeGreaterThan(0);
  });
});
