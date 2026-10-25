/** People helpers (mirror people.py so both dashboards say the same thing). */
import type { Person } from '../module_bindings/types';
import type { Box } from './live';

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

export class RenameError extends Error {
  constructor(
    message: string,
    readonly status?: number
  ) {
    super(message);
    this.name = 'RenameError';
  }
}

/**
 * Rename a person through FastAPI PUT /api/people/{id}. The endpoint replaces
 * every field, so the rest of the row is sent back unchanged. The bridge then
 * mirrors the new name into SpacetimeDB (within ~1.5 s).
 */
export async function renamePerson(
  person: Pick<Person, 'id' | 'description' | 'relationship' | 'knownSince'>,
  name: string,
  apiBase = '',
  fetchImpl: typeof fetch = fetch
): Promise<void> {
  let res: Response | undefined;
  try {
    res = await fetchImpl(`${apiBase.replace(/\/+$/, '')}/api/people/${String(person.id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name.trim(),
        description: person.description,
        relationship: person.relationship,
        known_since: person.knownSince || null,
      }),
    });
  } catch {
    throw new RenameError("Can't reach the RecallAI server to save the name.");
  }
  if (res?.ok) return;
  const detail = await errorDetail(res);
  throw new RenameError(
    res?.status === 404 ? 'That person no longer exists.' : `Couldn’t save the name${detail ? `: ${detail}` : '.'}`,
    res?.status
  );
}

async function errorDetail(res: Response | undefined): Promise<string> {
  try {
    const body = (await res?.json()) as { detail?: unknown } | undefined;
    return typeof body?.detail === 'string' ? body.detail : '';
  } catch {
    return ''; // non-JSON error body
  }
}

export type LabelResult = { personId: number; name: string; created: boolean; bbox: Box | null };

/**
 * Name the person in a live-feed box (POST /api/live/people). `personId`
 * says "this is that person"; otherwise `name` finds your person with that
 * name or creates them. The camera then saves a photo of them and puts the
 * name on their box from the next frames.
 */
export async function labelPerson(
  target: { bbox: Box; name?: string; personId?: number },
  apiBase = '',
  fetchImpl: typeof fetch = fetch
): Promise<LabelResult> {
  let res: Response | undefined;
  try {
    res = await fetchImpl(`${apiBase.replace(/\/+$/, '')}/api/live/people`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(
        target.personId !== undefined
          ? { bbox: target.bbox, person_id: target.personId }
          : { bbox: target.bbox, name: (target.name ?? '').trim() }
      ),
    });
  } catch {
    throw new RenameError("Can't reach the RecallAI server to save the name.");
  }
  if (!res?.ok) {
    const detail = await errorDetail(res);
    throw new RenameError(
      res?.status === 404 ? 'That person no longer exists.' : detail || 'Couldn’t save the name.',
      res?.status
    );
  }
  const body = (await res.json()) as { person?: { id?: unknown; name?: unknown }; created?: unknown; bbox?: unknown };
  const box = Array.isArray(body.bbox) && body.bbox.length === 4 ? (body.bbox.map(Number) as Box) : null;
  return {
    personId: Number(body.person?.id),
    name: typeof body.person?.name === 'string' ? body.person.name : (target.name ?? '').trim(),
    created: body.created === true,
    bbox: box && box.every(Number.isFinite) ? box : null,
  };
}

export type PersonDescription = {
  /** 3-6 words to spot them, e.g. "Black hoodie, round glasses". */
  summary: string;
  description: string;
  /** Grok's check that every photo shows the same person. */
  samePerson: 'yes' | 'no' | 'unsure';
  /** The camera's photos of them (API paths; resolveUrl adds ?token=). */
  photos: string[];
  /** Grok's description became their notes (they had none). */
  savedToNotes: boolean;
};

/**
 * Grok describes a person from the camera's photos of them
 * (POST /api/people/{id}/describe). Cached on the server until new photos
 * arrive; `refresh` asks Grok again.
 */
export async function describePerson(
  personId: bigint | number,
  refresh = false,
  apiBase = '',
  fetchImpl: typeof fetch = fetch
): Promise<PersonDescription> {
  let res: Response | undefined;
  try {
    res = await fetchImpl(`${apiBase.replace(/\/+$/, '')}/api/people/${String(personId)}/describe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh }),
    });
  } catch {
    throw new Error("Can't reach the RecallAI server.");
  }
  if (!res?.ok) {
    const detail = await errorDetail(res);
    throw new Error(res?.status === 404 ? 'That person no longer exists.' : detail || 'Khris couldn’t describe them.');
  }
  const body = (await res.json()) as Record<string, unknown>;
  const same = body.same_person;
  return {
    summary: typeof body.summary === 'string' ? body.summary : '',
    description: typeof body.description === 'string' ? body.description : '',
    samePerson: same === 'yes' || same === 'no' ? same : 'unsure',
    photos: Array.isArray(body.photos) ? body.photos.filter((p): p is string => typeof p === 'string') : [],
    savedToNotes: body.saved_to_notes === true,
  };
}
