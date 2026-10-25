import type { Alert, Belonging, Sighting } from '../module_bindings/types';
import { resolveUrl } from './url';

/** verified: -1 = unchecked by Grok, 0 = Grok says "not yours" (hidden), 1 = confirmed. */
export function isVisible(s: Pick<Sighting, 'verified'>): boolean {
  return s.verified !== 0;
}

/** Newest first; ties broken by higher id (later insert). */
export function compareNewestFirst(
  a: Pick<Sighting, 'seenAtMicros' | 'id'>,
  b: Pick<Sighting, 'seenAtMicros' | 'id'>
): number {
  if (a.seenAtMicros !== b.seenAtMicros) return a.seenAtMicros > b.seenAtMicros ? -1 : 1;
  if (a.id !== b.id) return a.id > b.id ? -1 : 1;
  return 0;
}

/** Visible sightings grouped by belongingId, each list sorted newest first. */
export function groupVisibleByBelonging(
  sightings: readonly Sighting[]
): Map<string, Sighting[]> {
  const out = new Map<string, Sighting[]>();
  for (const s of sightings) {
    if (!isVisible(s) || !s.belongingId) continue;
    let list = out.get(s.belongingId);
    if (!list) out.set(s.belongingId, (list = []));
    list.push(s);
  }
  for (const list of out.values()) list.sort(compareNewestFirst);
  return out;
}

/** Latest visible sighting for each belonging id. */
export function latestVisibleByBelonging(
  sightings: readonly Sighting[]
): Map<string, Sighting> {
  const out = new Map<string, Sighting>();
  for (const s of sightings) {
    if (!isVisible(s) || !s.belongingId) continue;
    const cur = out.get(s.belongingId);
    if (!cur || compareNewestFirst(s, cur) < 0) out.set(s.belongingId, s);
  }
  return out;
}

/** Timeline for one belonging: visible only, newest first, capped. */
export function timelineFor(
  sightings: readonly Sighting[],
  belongingId: string,
  limit = 50
): Sighting[] {
  return sightings
    .filter(s => s.belongingId === belongingId && isVisible(s))
    .sort(compareNewestFirst)
    .slice(0, limit);
}

/** Belongings sorted: most recently seen first, never-seen last (by name). */
export function sortBelongings(
  belongings: readonly Belonging[],
  latest: Map<string, Sighting>
): Belonging[] {
  return [...belongings].sort((a, b) => {
    const la = latest.get(a.id);
    const lb = latest.get(b.id);
    if (la && lb) return compareNewestFirst(la, lb);
    if (la) return -1;
    if (lb) return 1;
    return a.name.localeCompare(b.name);
  });
}

/** Non-dismissed alerts, newest first. */
export function activeAlerts(alerts: readonly Alert[]): Alert[] {
  return alerts
    .filter(a => !a.dismissed)
    .sort((a, b) =>
      a.createdAtMicros === b.createdAtMicros
        ? a.id > b.id ? -1 : 1
        : a.createdAtMicros > b.createdAtMicros ? -1 : 1
    );
}

/** Index sightings by stringified id for O(1) lookup (bigint ids aren't safe Map keys across copies). */
export function indexById(sightings: readonly Sighting[]): Map<string, Sighting> {
  const m = new Map<string, Sighting>();
  for (const s of sightings) m.set(String(s.id), s);
  return m;
}

/** Thumbnail: the crop if there is one, else the full frame. */
export function thumbUrl(s: Pick<Sighting, 'cropUrl' | 'imageUrl'>, apiBase = ''): string {
  return resolveUrl(s.cropUrl || s.imageUrl, apiBase);
}

export function frameUrl(s: Pick<Sighting, 'cropUrl' | 'imageUrl'>, apiBase = ''): string {
  return resolveUrl(s.imageUrl || s.cropUrl, apiBase);
}

/** "laptop, bottle" -> ["laptop", "bottle"] (trimmed, de-duplicated, empties dropped). */
export function splitNearby(nearby: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of nearby.split(',')) {
    const t = raw.trim();
    if (!t || seen.has(t.toLowerCase())) continue;
    seen.add(t.toLowerCase());
    out.push(t);
  }
  return out;
}

export type EventKind = 'seen' | 'left_view';

export function eventLabel(event: string): string {
  return event === 'left_view' ? 'Left view' : 'Seen';
}
