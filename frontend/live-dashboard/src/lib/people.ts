/** People helpers (mirror people.py so both dashboards say the same thing). */
import type { Person } from '../module_bindings/types';

function n(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

/** Parse "YYYY-MM-DD" as a calendar date (no timezone shifts). */
function parseDay(text: string): { y: number; m: number; d: number } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(text);
  if (!match) return null;
  const [y, m, d] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const check = new Date(Date.UTC(y, m - 1, d));
  if (check.getUTCMonth() !== m - 1 || check.getUTCDate() !== d) return null;
  return { y, m, d };
}

/**
 * "2 years and 3 months" since knownSince (YYYY-MM-DD), like people.known_for.
 * Returns null for "" / invalid / future dates.
 */
export function knownFor(knownSince: string, now: Date = new Date()): string | null {
  const start = knownSince ? parseDay(knownSince) : null;
  if (!start) return null;
  const today = { y: now.getFullYear(), m: now.getMonth() + 1, d: now.getDate() };
  const startUtc = Date.UTC(start.y, start.m - 1, start.d);
  const todayUtc = Date.UTC(today.y, today.m - 1, today.d);
  if (startUtc > todayUtc) return null;
  let years = today.y - start.y;
  let months = today.m - start.m;
  if (today.d < start.d) months -= 1;
  if (months < 0) {
    years -= 1;
    months += 12;
  }
  const parts: string[] = [];
  if (years) parts.push(n(years, 'year'));
  if (months) parts.push(n(months, 'month'));
  if (parts.length) return parts.join(' and ');
  const days = Math.round((todayUtc - startUtc) / 86_400_000);
  return days === 0 ? 'since today' : n(days, 'day');
}

/** Alphabetical (case-insensitive), then by id — same order as GET /api/people. */
export function sortPeople(people: readonly Person[]): Person[] {
  return [...people].sort((a, b) => {
    const c = a.name.toLowerCase().localeCompare(b.name.toLowerCase());
    if (c) return c;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}
