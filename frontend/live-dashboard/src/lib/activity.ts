import type { Sighting } from '../module_bindings/types';

export type ActivityKind = 'new' | 'described' | 'updated';

export type ActivityItem = {
  key: string;
  kind: ActivityKind;
  sighting: Sighting;
  /** When the dashboard received the change (ms since epoch). */
  receivedAt: number;
};

/** Classify an update: a description arriving from Grok is the interesting case. */
export function classifyUpdate(oldRow: Sighting, newRow: Sighting): ActivityKind {
  if (!oldRow.description && newRow.description) return 'described';
  return 'updated';
}

let seq = 0;

/** Prepend an item, collapsing a repeated entry for the same sighting, capped at `max`. */
export function pushActivity(
  feed: readonly ActivityItem[],
  kind: ActivityKind,
  sighting: Sighting,
  receivedAt: number,
  max = 10
): ActivityItem[] {
  const id = String(sighting.id);
  const rest = feed.filter(i => String(i.sighting.id) !== id);
  return [{ key: `${id}-${++seq}`, kind, sighting, receivedAt }, ...rest].slice(0, max);
}

export function activityText(item: ActivityItem): string {
  const name = item.sighting.belongingName || item.sighting.objectName || 'Item';
  switch (item.kind) {
    case 'new':
      return item.sighting.event === 'left_view' ? `${name} left view` : `${name} spotted`;
    case 'described':
      return `Khris described ${name}`;
    default:
      return `${name} updated`;
  }
}
