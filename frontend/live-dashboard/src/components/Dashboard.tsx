import { useEffect, useMemo, useRef, useState } from 'react';
import { useCursorLens } from '../hooks/useCursorLens';
import type { Alert, Belonging, Person, Sighting } from '../module_bindings/types';
import type { ActivityItem } from '../lib/activity';
import type { Box, CameraState } from '../lib/live';
import type { LabelResult, PersonDescription } from '../lib/people';
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
import type { Enroll } from '../hooks/useEnroll';
import { CheckIcon, PlusIcon, TrashIcon, Wave } from './Icons';
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
  /** Names the person in a live-feed box (double-click it). Omitted -> people boxes are read-only. */
  onLabelPerson?: (bbox: Box, name: string, personId?: number) => Promise<LabelResult>;
  /** Grok describes a person from the camera's photos (click them in People). */
  onDescribePerson?: (person: Person, refresh: boolean) => Promise<PersonDescription>;
  initialSelectedId?: string | null;
  /** The signed-in account shown in the top bar, with its sign-out action. */
  account?: { name: string; username: string; onSignOut: () => void };
  /** Rough place from the browser's IP ("Irvine, CA"), shown until there's a GPS fix. */
  ipLocation?: string;
  /** Deletes belongings (trash button). Resolves with the ids deleted. Omitted -> no trash button. */
  onDeleteBelongings?: (ids: string[]) => Promise<string[]>;
  /** Adding a belonging with the camera ("+" button). Omitted -> no "+" button. */
  enroll?: Enroll;
  /** Renames a belonging (double-click its name, or the pencil). Omitted -> names are read-only. */
  onRenameBelonging?: (id: string, name: string) => Promise<unknown>;
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

/** Progress of adding a belonging with the camera ("+" button). */
function EnrollBanner({ enroll }: { enroll: Enroll }) {
  const { state, error } = enroll;
  if (!state && !error) return null;
  if (!state) {
    return (
      <p className="notice notice-error small" role="alert">
        {error}
      </p>
    );
  }
  const done = state.phase === 'done' && !state.active;
  const busy = state.phase === 'busy';
  const needed = Math.max(state.needed, 1);
  const saved = Math.min(state.saved, needed);
  return (
    <div className={`enroll-banner${done ? ' is-done' : ''}${busy ? ' is-busy' : ''}`} role="status" aria-live="polite">
      <span className="enroll-icon" aria-hidden="true">
        {done ? <CheckIcon /> : <PlusIcon size={14} />}
      </span>
      <span className="enroll-text">
        <span className="enroll-title">
          {done ? `${state.name || 'Item'} added` : busy ? 'Camera busy' : state.name ? `Adding ${state.name}` : 'Adding a belonging'}
        </span>
        <span className="enroll-sub">
          {done
            ? 'It’s in your belongings and the camera is tracking it.'
            : state.message || 'Hold one object up to the camera and keep it still.'}
        </span>
      </span>
      {!busy && (
        <span className="enroll-steps" aria-label={`${saved} of ${needed} angles saved`}>
          {Array.from({ length: needed }, (_, i) => (
            <span key={i} className={`enroll-step${i < saved ? ' is-on' : ''}`} />
          ))}
          <span className="mono small">
            {saved}/{needed}
          </span>
        </span>
      )}
      {state.active && !busy && (
        <button type="button" className="tool-btn" onClick={() => void enroll.cancel()}>
          Cancel
        </button>
      )}
    </div>
  );
}

/** Pure presentational dashboard: everything comes in through props. */
export function Dashboard(props: DashboardProps) {
  const { status, live, belongings, sightings, alerts, activity, now, apiBase, dismissing, ask } = props;
  const [selectedId, setSelectedId] = useState<string | null>(props.initialSelectedId ?? null);
  const [lightbox, setLightbox] = useState<{ src: string; caption?: string } | null>(null);
  const [filter, setFilter] = useState<Filter>('all');
  // Trash mode: pick cards, then delete them. `removed` hides them until SpacetimeDB drops them.
  const [picking, setPicking] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [removed, setRemoved] = useState<Set<string>>(() => new Set());
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const latest = useMemo(() => latestVisibleByBelonging(sightings), [sightings]);
  const kept = useMemo(() => belongings.filter(b => !removed.has(b.id)), [belongings, removed]);
  const sorted = useMemo(() => sortBelongings(kept, latest), [kept, latest]);
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
  // GPS of the newest visible sighting that has a fix: "(37.8700, -122.2600)".
  const newestCoords = useMemo(() => {
    let best: Sighting | undefined;
    for (const s of latest.values()) if (s.hasGps && (!best || s.seenAtMicros > best.seenAtMicros)) best = s;
    return best ? `(${best.latitude.toFixed(4)}, ${best.longitude.toFixed(4)})` : undefined;
  }, [latest]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (lightbox) setLightbox(null);
      else if (picking) stopPicking();
      else setSelectedId(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lightbox, picking]);

  function stopPicking() {
    setPicking(false);
    setPicked(new Set());
    setDeleteError(null);
  }
  const togglePick = (id: string) =>
    setPicked(prev => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const deletePicked = async () => {
    const ids = [...picked];
    if (!ids.length || !props.onDeleteBelongings) return;
    const what = ids.length === 1 ? (belongings.find(b => b.id === ids[0])?.name ?? 'this belonging') : `${ids.length} belongings`;
    if (!window.confirm(`Delete ${what}? Their photos and sighting history are removed for good.`)) return;
    setDeleting(true);
    setDeleteError(null);
    const hide = (gone: string[]) => setRemoved(prev => new Set([...prev, ...gone]));
    try {
      hide(await props.onDeleteBelongings(ids));
      if (selectedId && ids.includes(selectedId)) setSelectedId(null);
      stopPicking();
    } catch (err) {
      const gone = (err as { deleted?: string[] }).deleted ?? [];
      hide(gone);
      setPicked(prev => new Set([...prev].filter(id => !gone.includes(id))));
      setDeleteError(err instanceof Error ? err.message : 'Couldn’t delete them.');
    } finally {
      setDeleting(false);
    }
  };

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
                {newestCoords ?? `Location - ${props.ipLocation ?? 'I.P.'}`} -{' '}
                <LiveClock weekday />
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

          <LiveFeed
            camera={props.camera}
            missing={missing}
            now={now}
            onSelect={setSelectedId}
            people={props.people}
            onRenamePerson={props.onRenamePerson}
            onLabelPerson={props.onLabelPerson}
          />

          <section aria-labelledby="belongings-title">
            <header className="section-head">
              <h2 id="belongings-title">Your belongings</h2>
              <div className="section-tools">
                {picking ? (
                  <>
                    <span className="muted small" role="status">
                      {picked.size ? `${picked.size} selected` : 'Select items to delete'}
                    </span>
                    <button type="button" className="tool-btn" onClick={stopPicking} disabled={deleting}>
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="tool-btn tool-btn-danger"
                      onClick={() => void deletePicked()}
                      disabled={!picked.size || deleting}
                    >
                      <TrashIcon size={14} /> {deleting ? 'Deleting…' : `Delete${picked.size ? ` ${picked.size}` : ''}`}
                    </button>
                  </>
                ) : (
                  <>
                    <span className="muted small">Select a card for its history</span>
                    {props.enroll && (
                      <button
                        type="button"
                        className="tool-btn add-btn"
                        onClick={() => void props.enroll!.start()}
                        disabled={props.enroll.state?.active}
                        aria-label="Add a belonging with the camera"
                        title="Add a belonging with the camera"
                      >
                        <PlusIcon />
                      </button>
                    )}
                    {props.onDeleteBelongings && sorted.length > 0 && (
                      <button
                        type="button"
                        className="tool-btn trash-btn"
                        onClick={() => setPicking(true)}
                        aria-label="Select belongings to delete"
                        title="Select belongings to delete"
                      >
                        <TrashIcon />
                      </button>
                    )}
                  </>
                )}
              </div>
            </header>
            {deleteError && (
              <p className="notice notice-error small" role="alert">
                {deleteError}
              </p>
            )}
            {props.enroll && <EnrollBanner enroll={props.enroll} />}
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
                    No belongings registered yet. Run <code>python -m recallai.vision.register</code> to add one.
                  </p>
                )}
              </div>
            ) : shown.length === 0 ? (
              <div className="empty-state">
                <p>Nothing in “{FILTERS.find(([id]) => id === filter)?.[1]}” right now.</p>
              </div>
            ) : (
              <div className={`grid${picking ? ' is-picking' : ''}`}>
                {shown.map((b, i) => (
                  <div
                    key={b.id}
                    className={`grid-cell${picked.has(b.id) ? ' is-picked' : ''}`}
                    style={{ ['--i' as string]: Math.min(i, 8) }}
                  >
                    {picking && (
                      <button
                        type="button"
                        className="pick-hit"
                        aria-pressed={picked.has(b.id)}
                        aria-label={`${picked.has(b.id) ? 'Unselect' : 'Select'} ${b.name}`}
                        onClick={() => togglePick(b.id)}
                      >
                        <span className="pick-check" aria-hidden="true">
                          {picked.has(b.id) && <CheckIcon />}
                        </span>
                      </button>
                    )}
                    <BelongingCard
                      belonging={b}
                      latest={latest.get(b.id)}
                      now={now}
                      apiBase={apiBase}
                      live={live}
                      leftAfter={props.leftAfter}
                      onSelect={setSelectedId}
                      onRename={props.onRenameBelonging}
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
          <PeoplePanel
            people={props.people ?? []}
            now={now}
            onRename={props.onRenamePerson}
            onDescribe={props.onDescribePerson}
            apiBase={apiBase}
          />
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
