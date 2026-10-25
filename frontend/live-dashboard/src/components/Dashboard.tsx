import { useEffect, useMemo, useRef, useState } from 'react';
import { useCursorLens } from '../hooks/useCursorLens';
import type { Alert, Belonging, Person, Sighting } from '../module_bindings/types';
import type { ActivityItem } from '../lib/activity';
import type { CameraState } from '../lib/live';
import {
  activeAlerts,
  indexById,
  latestVisibleByBelonging,
  sortBelongings,
  timelineFor,
} from '../lib/sightings';
import { formatShortAgo, microsToDate } from '../lib/time';
import { ActivityFeed } from './ActivityFeed';
import { AlertsBanner } from './AlertsBanner';
import { AskPanel } from './AskPanel';
import { BelongingCard } from './BelongingCard';
import { Header } from './Header';
import { Wave } from './Icons';
import { Lightbox } from './Lightbox';
import { LiveClock, LiveFeed } from './LiveFeed';
import { PeoplePanel } from './PeoplePanel';
import { TimelineDrawer } from './TimelineDrawer';
import type { AskEntry, ConnectionStatus } from './types';

export type DashboardProps = {
  status: ConnectionStatus;
  /** True once the initial subscription has loaded (enables "just updated" highlights). */
  live: boolean;
  belongings: readonly Belonging[];
  sightings: readonly Sighting[];
  alerts: readonly Alert[];
  /** People mirrored from SQLite `people`; optional so older callers still work. */
  people?: readonly Person[];
  activity: ActivityItem[];
  now: Date;
  apiBase: string;
  dismissing: Set<string>;
  dismissError?: string | null;
  onDismissAlert: (id: bigint) => void;
  ask: {
    entries: AskEntry[];
    pending: boolean;
    onAsk: (q: string) => void;
    onClear: () => void;
  };
  /** The Python camera's live view (GET /api/live). Omitted -> the panel shows "camera is off". */
  camera?: CameraState;
  /** Minutes out of view before a card shows the left-behind timer. */
  leftAfter?: number;
  /** Saves a person's new name (double-click a name in People). Omitted -> read-only. */
  onRenamePerson?: (person: Person, name: string) => Promise<void>;
  initialSelectedId?: string | null;
  /** The signed-in account shown in the top bar, with its sign-out action. */
  account?: { name: string; username: string; onSignOut: () => void };
};

type Filter = 'all' | 'seen' | 'left' | 'enrolling' | 'never';

const FILTERS: [Filter, string][] = [
  ['all', 'All'],
  ['seen', 'Seen'],
  ['left', 'Left view'],
  ['enrolling', 'Enrolling'],
  ['never', 'Not seen yet'],
];

function statusOf(b: Belonging, latest: Sighting | undefined): Exclude<Filter, 'all'> {
  if (!b.tracking) return 'enrolling';
  if (!latest) return 'never';
  return latest.event === 'left_view' ? 'left' : 'seen';
}

/** Pure presentational dashboard: everything comes in through props. */
export function Dashboard(props: DashboardProps) {
  const { status, live, belongings, sightings, alerts, activity, now, apiBase, dismissing, ask } = props;
  const [selectedId, setSelectedId] = useState<string | null>(props.initialSelectedId ?? null);
  const [lightbox, setLightbox] = useState<{ src: string; caption?: string } | null>(null);
  const [filter, setFilter] = useState<Filter>('all');

  const latest = useMemo(() => latestVisibleByBelonging(sightings), [sightings]);
  const sorted = useMemo(() => sortBelongings(belongings, latest), [belongings, latest]);
  const byId = useMemo(() => indexById(sightings as Sighting[]), [sightings]);
  const shownAlerts = useMemo(
    () => activeAlerts(alerts).filter(a => !dismissing.has(String(a.id))),
    [alerts, dismissing]
  );
  const visibleCount = useMemo(() => sightings.filter(s => s.verified !== 0).length, [sightings]);
  const selected = selectedId ? belongings.find(b => b.id === selectedId) : undefined;
  const timeline = useMemo(
    () => (selectedId ? timelineFor(sightings, selectedId) : []),
    [sightings, selectedId]
  );

  const statuses = useMemo(() => new Map(sorted.map(b => [b.id, statusOf(b, latest.get(b.id))])), [sorted, latest]);
  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: sorted.length, seen: 0, left: 0, enrolling: 0, never: 0 };
    for (const s of statuses.values()) c[s] += 1;
    return c;
  }, [sorted, statuses]);
  const shown = filter === 'all' ? sorted : sorted.filter(b => statuses.get(b.id) === filter);
  const missing = useMemo(
    () =>
      sorted
        .filter(b => statuses.get(b.id) === 'left')
        .map(b => ({ belonging: b, sighting: latest.get(b.id)! })),
    [sorted, statuses, latest]
  );
  // Newest visible sighting overall: drives "where" in the heading and the Live pill's age.
  const newest = sorted.length ? latest.get(sorted[0].id) : undefined;
  const newestAge = newest ? formatShortAgo(microsToDate(newest.seenAtMicros), now) : undefined;
  const newestPlace = useMemo(() => {
    let best: Sighting | undefined;
    for (const s of latest.values()) if (s.place && (!best || s.seenAtMicros > best.seenAtMicros)) best = s;
    return best?.place;
  }, [latest]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (lightbox) setLightbox(null);
      else setSelectedId(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lightbox]);

  const openImage = (src: string, caption?: string) => setLightbox({ src, caption });
  const appRef = useRef<HTMLDivElement>(null);
  useCursorLens(appRef);

  return (
    <div className="app gridbg" ref={appRef}>
      <Header
        status={status}
        belongings={belongings.length}
        sightings={visibleCount}
        alerts={shownAlerts.length}
        updated={newestAge && (newestAge === 'now' ? 'just now' : `${newestAge} ago`)}
        account={props.account}
      />
      <main className="layout">
        <div className="col-main">
          <div className="page-head">
            <div className="page-head-row">
              <p className="eyebrow mono page-eyebrow">
                {newestPlace ? `Last seen at ${newestPlace}` : 'Your belongings'} · <LiveClock weekday />
              </p>
              {sorted.length > 0 && (
                <div className="filters" role="group" aria-label="Filter belongings">
                  {FILTERS.filter(([id]) => id !== 'never' || counts.never > 0).map(([id, label]) => (
                    <button
                      key={id}
                      type="button"
                      className={`filter${filter === id ? ' filter-on' : ''}`}
                      aria-pressed={filter === id}
                      onClick={() => setFilter(id)}
                    >
                      {label} <span className="mono filter-count">{counts[id]}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <Wave />
          </div>

          {props.dismissError && (
            <p className="notice notice-error" role="alert">
              {props.dismissError}
            </p>
          )}
          <AlertsBanner
            alerts={shownAlerts}
            sightingsById={byId}
            now={now}
            apiBase={apiBase}
            dismissing={dismissing}
            onDismiss={props.onDismissAlert}
            onOpenImage={openImage}
          />

          <LiveFeed camera={props.camera} missing={missing} now={now} onSelect={setSelectedId} />

          <section aria-labelledby="belongings-title">
            <header className="section-head">
              <h2 id="belongings-title">Your belongings</h2>
              <span className="muted small">Select a card for its history</span>
            </header>
            {sorted.length === 0 ? (
              <div className="empty-state">
                {status === 'connecting' ? (
                  <p>Loading your belongings…</p>
                ) : status === 'disconnected' ? (
                  <p>
                    Can’t reach the live database. Retrying… (Developers: is <code>spacetime start</code> running?)
                  </p>
                ) : (
                  <p>
                    No belongings registered yet. Press <strong>Add</strong> in the camera dashboard, or run{' '}
                    <code>python -m recallai.vision.register</code>.
                  </p>
                )}
              </div>
            ) : shown.length === 0 ? (
              <div className="empty-state">
                <p>Nothing in “{FILTERS.find(([id]) => id === filter)?.[1]}” right now.</p>
              </div>
            ) : (
              <div className="grid">
                {shown.map((b, i) => (
                  <div key={b.id} className="grid-cell" style={{ ['--i' as string]: Math.min(i, 8) }}>
                    <BelongingCard
                      belonging={b}
                      latest={latest.get(b.id)}
                      now={now}
                      apiBase={apiBase}
                      live={live}
                      leftAfter={props.leftAfter}
                      onSelect={setSelectedId}
                    />
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
        <aside className="col-side">
          <AskPanel
            entries={ask.entries}
            pending={ask.pending}
            onAsk={ask.onAsk}
            onClear={ask.onClear}
            sightingsById={byId}
            apiBase={apiBase}
            now={now}
            onOpenImage={openImage}
          />
          <ActivityFeed items={activity} now={now} onSelect={setSelectedId} />
          <PeoplePanel people={props.people ?? []} now={now} onRename={props.onRenamePerson} />
        </aside>
      </main>
      {selected && (
        <TimelineDrawer
          belonging={selected}
          sightings={timeline}
          now={now}
          apiBase={apiBase}
          onClose={() => setSelectedId(null)}
          onOpenImage={openImage}
        />
      )}
      {lightbox && <Lightbox src={lightbox.src} caption={lightbox.caption} onClose={() => setLightbox(null)} />}
    </div>
  );
}
