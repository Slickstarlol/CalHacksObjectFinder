import { useEffect, useMemo, useState } from 'react';
import type { Alert, Belonging, Person, Sighting } from '../module_bindings/types';
import type { ActivityItem } from '../lib/activity';
import {
  activeAlerts,
  indexById,
  latestVisibleByBelonging,
  sortBelongings,
  timelineFor,
} from '../lib/sightings';
import { ActivityFeed } from './ActivityFeed';
import { AlertsBanner } from './AlertsBanner';
import { AskPanel } from './AskPanel';
import { BelongingCard } from './BelongingCard';
import { Header } from './Header';
import { Lightbox } from './Lightbox';
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
  initialSelectedId?: string | null;
  /** The signed-in account shown in the top bar, with its sign-out action. */
  account?: { name: string; username: string; onSignOut: () => void };
};

/** Pure presentational dashboard: everything comes in through props. */
export function Dashboard(props: DashboardProps) {
  const { status, live, belongings, sightings, alerts, activity, now, apiBase, dismissing, ask } = props;
  const [selectedId, setSelectedId] = useState<string | null>(props.initialSelectedId ?? null);
  const [lightbox, setLightbox] = useState<{ src: string; caption?: string } | null>(null);

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

  return (
    <div className="app">
      <Header
        status={status}
        belongings={belongings.length}
        sightings={visibleCount}
        alerts={shownAlerts.length}
        account={props.account}
      />
      <main className="layout">
        <div className="col-main">
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
                    No belongings registered yet. Run <code>python -m recallai.vision.register</code> to add one.
                  </p>
                )}
              </div>
            ) : (
              <div className="grid">
                {sorted.map(b => (
                  <BelongingCard
                    key={b.id}
                    belonging={b}
                    latest={latest.get(b.id)}
                    now={now}
                    apiBase={apiBase}
                    live={live}
                    onSelect={setSelectedId}
                  />
                ))}
              </div>
            )}
          </section>
        </div>
        <div className="col-side">
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
          <ActivityFeed items={activity} now={now} apiBase={apiBase} onSelect={setSelectedId} />
          <PeoplePanel people={props.people ?? []} now={now} />
        </div>
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
