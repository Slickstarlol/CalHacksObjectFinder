import { useCallback, useEffect, useRef, useState } from 'react';
import { useReducer, useSpacetimeDB, useTable } from 'spacetimedb/react';
import { reducers, tables } from './module_bindings';
import type { Sighting } from './module_bindings/types';
import { Dashboard } from './components/Dashboard';
import type { ConnectionStatus } from './components/types';
import { useAsk } from './hooks/useAsk';
import { useNow } from './hooks/useNow';
import { classifyUpdate, pushActivity, type ActivityItem } from './lib/activity';
import { isVisible } from './lib/sightings';
import { API_BASE } from './config';

/** Thin container: wires SpacetimeDB hooks + the ask API into the presentational Dashboard. */
export default function App() {
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
    if (!sightingsReady.current || !isVisible(row)) return;
    setActivity(prev => pushActivity(prev, kind, row, Date.now()));
  }, []);

  const [belongings, belongingsReady] = useTable(tables.belonging);
  const [sightings, sightingsLoaded] = useTable(tables.sighting, {
    onInsert: row => record('new', row),
    onUpdate: (oldRow, newRow) => record(classifyUpdate(oldRow, newRow), newRow),
  });
  const [alerts] = useTable(tables.alert.where(r => r.dismissed.eq(false)));
  const [people] = useTable(tables.person);
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
  const ask = useAsk(API_BASE);

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
    />
  );
}
