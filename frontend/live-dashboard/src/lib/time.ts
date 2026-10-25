/** Time helpers. SpacetimeDB i64/u64 columns arrive as bigint microseconds. */

/** bigint microseconds since the Unix epoch -> Date (millisecond precision). */
export function microsToDate(micros: bigint): Date {
  return new Date(Number(micros / 1000n));
}

/** bigint microseconds -> milliseconds as a JS number. */
export function microsToMillis(micros: bigint): number {
  return Number(micros / 1000n);
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

function plural(n: number, unit: string): string {
  return `${n} ${unit}${n === 1 ? '' : 's'} ago`;
}

/**
 * Human relative time: "just now", "1 minute ago", "23 minutes ago",
 * "2 hours ago", "yesterday", "3 days ago", then a short date.
 * Future times (clock skew between webcam host and browser) read as "just now".
 */
export function formatRelative(date: Date, now: Date = new Date()): string {
  const diff = now.getTime() - date.getTime();
  if (diff < 45_000) return 'just now';
  if (diff < HOUR) return plural(Math.max(1, Math.round(diff / MINUTE)), 'minute');
  if (diff < DAY) return plural(Math.floor(diff / HOUR), 'hour');
  if (diff < 2 * DAY) return 'yesterday';
  if (diff < 7 * DAY) return `${Math.floor(diff / DAY)} days ago`;
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: date.getFullYear() === now.getFullYear() ? undefined : 'numeric',
  });
}

/** Compact relative time for tight spots: "now", "4m", "2h", "3d". */
export function formatShortAgo(date: Date, now: Date = new Date()): string {
  const diff = now.getTime() - date.getTime();
  if (diff < 45_000) return 'now';
  if (diff < HOUR) return `${Math.max(1, Math.round(diff / MINUTE))}m`;
  if (diff < DAY) return `${Math.floor(diff / HOUR)}h`;
  return `${Math.floor(diff / DAY)}d`;
}

/** Whole minutes since `date` (0 for future times). */
export function minutesSince(date: Date, now: Date = new Date()): number {
  return Math.max(0, Math.floor((now.getTime() - date.getTime()) / MINUTE));
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

/** "3:42 PM" for today, otherwise "Oct 22, 3:42 PM". */
export function formatClock(date: Date, now: Date = new Date()): string {
  const time = date.toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  });
  if (sameDay(date, now)) return time;
  const day = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  return `${day}, ${time}`;
}

/** Full timestamp for title/tooltip attributes. */
export function formatFull(date: Date): string {
  return date.toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'medium',
  });
}

/** The browser's IANA time zone, or "UTC" if unavailable. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}
