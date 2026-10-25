import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useReducer, useSpacetimeDB, useTable } from 'spacetimedb/react';
import { reducers, tables } from './module_bindings';
import type { Sighting } from './module_bindings/types';
import { Dashboard } from './components/Dashboard';
import type { ConnectionStatus } from './components/types';
import { useAsk } from './hooks/useAsk';
import { useLiveCamera } from './hooks/useLiveCamera';
import { useNow } from './hooks/useNow';
import { classifyUpdate, pushActivity, type ActivityItem } from './lib/activity';
import { isVisible } from './lib/sightings';
import { ownedBy, ownerKey, type AuthUser } from './lib/auth';
import { API_BASE } from './config';

export type AppProps = {
  /** The signed-in RecallAI account. Only its rows are subscribed to and shown. */
  user: AuthUser;
  /** fetch() that sends the bearer token (and signs out on a 401). */
  authFetch: typeof fetch;
  onSignOut: () => void;
};

/**
 * Thin container: wires SpacetimeDB hooks + the ask API into the presentational Dashboard.
 *
 * Every subscription is filtered to `owner = <user id>`, so SpacetimeDB only
 * sends this user's rows, and rows are filtered again before rendering. The
 * tables are public, so this is privacy-by-filtering for a local app, not
 * access control (that would need SpacetimeDB row-level security).
 */
export default function App({ user, authFetch, onSignOut }: AppProps) {
  const owner = ownerKey(user);
  const { isActive, connectionError } = useSpacetimeDB();
  const [everConnected, setEverConnected] = useState(false);
  useEffect(() => {
    if (isActive) setEverConnected(true);
  }, [isActive]);
  const status: ConnectionStatus = isActive
    ? 'live'
    : everConnected || connectionError
      ? 'disconnected'
      : 'connecting';

  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const sightingsReady = useRef(false);
  const record = useCallback((kind: ActivityItem['kind'], row: Sighting) => {
    // Initial subscription rows also arrive as inserts; only log live changes.
    if (!sightingsReady.current || !isVisible(row) || row.owner !== owner) return;
    setActivity(prev => pushActivity(prev, kind, row, Date.now()));
  }, [owner]);

  const [allBelongings, belongingsReady] = useTable(tables.belonging.where(r => r.owner.eq(owner)));
  const [allSightings, sightingsLoaded] = useTable(tables.sighting.where(r => r.owner.eq(owner)), {
    onInsert: row => record('new', row),
    onUpdate: (oldRow, newRow) => record(classifyUpdate(oldRow, newRow), newRow),
  });
  const [allAlerts] = useTable(tables.alert.where(r => r.dismissed.eq(false).and(r.owner.eq(owner))));
  const [allPeople] = useTable(tables.person.where(r => r.owner.eq(owner)));
  const belongings = useMemo(() => ownedBy(allBelongings, owner), [allBelongings, owner]);
  const sightings = useMemo(() => ownedBy(allSightings, owner), [allSightings, owner]);
  const alerts = useMemo(() => ownedBy(allAlerts, owner), [allAlerts, owner]);
  const people = useMemo(() => ownedBy(allPeople, owner), [allPeople, owner]);
  useEffect(() => {
    sightingsReady.current = sightingsLoaded;
  }, [sightingsLoaded]);

  const dismissAlert = useReducer(reducers.dismissAlert);
  const [dismissing, setDismissing] = useState<Set<string>>(() => new Set());
  const [dismissError, setDismissError] = useState<string | null>(null);
  const onDismissAlert = useCallback(
    (id: bigint) => {
      const key = String(id);
      setDismissError(null);
      setDismissing(prev => new Set(prev).add(key));
      const undo = () =>
        setDismissing(prev => {
          const next = new Set(prev);
          next.delete(key);
          return next;
        });
      Promise.resolve()
        .then(() => dismissAlert({ id }))
        .then(() => setTimeout(undo, 4000)) // row update will have removed it by then
        .catch(err => {
          undo();
          setDismissError(`Couldn't dismiss the alert: ${err?.message ?? err}`);
        });
    },
    [dismissAlert]
  );

  const now = useNow(30_000);
  const ask = useAsk(API_BASE, authFetch);
  const camera = useLiveCamera(API_BASE, authFetch);

  return (
    <Dashboard
      status={status}
      live={belongingsReady && sightingsLoaded}
      belongings={belongings}
      sightings={sightings}
      alerts={alerts}
      people={people}
      activity={activity}
      now={now}
      apiBase={API_BASE}
      dismissing={dismissing}
      dismissError={dismissError}
      onDismissAlert={onDismissAlert}
      ask={{ entries: ask.entries, pending: ask.pending, onAsk: ask.ask, onClear: ask.clear }}
      camera={camera}
      account={{ name: user.display_name || user.username, username: user.username, onSignOut }}
    />
  );
}
