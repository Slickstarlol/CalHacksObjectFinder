/**
 * Dev-only demo (http://localhost:5174/demo.html): renders the presentational
 * Dashboard with fixture data, no SpacetimeDB or FastAPI needed.
 * Query params: ?theme=dark|light  ?drawer=<belongingId>  ?simulate=1  ?empty=1
 */
import { StrictMode, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Dashboard } from '../components/Dashboard';
import type { AskEntry } from '../components/types';
import { pushActivity, type ActivityItem } from '../lib/activity';
import { useNow } from '../hooks/useNow';
import type { Sighting } from '../module_bindings/types';
import {
  askEntries,
  belongings,
  makeActivity,
  makeAlerts,
  makeSighting,
  makeSightings,
  micros,
} from './fixtures';
import '../styles.css';

const params = new URLSearchParams(location.search);
const theme = params.get('theme');
if (theme === 'dark' || theme === 'light') document.documentElement.dataset.theme = theme;

function Demo() {
  const start = useMemo(() => Date.now(), []);
  const [sightings, setSightings] = useState<Sighting[]>(() => makeSightings(start));
  const [alerts, setAlerts] = useState(() => makeAlerts(start));
  const [activity, setActivity] = useState<ActivityItem[]>(() => makeActivity(start, sightings));
  const [entries, setEntries] = useState<AskEntry[]>(askEntries);
  const now = useNow(30_000);

  useEffect(() => {
    if (params.get('simulate') !== '1') return;
    let id = 2000n;
    const t = setInterval(() => {
      const s = makeSighting({
        id: ++id,
        belongingId: '3617519a583a',
        belongingName: 'My Bag',
        objectName: 'backpack',
        seenAtMicros: micros(Date.now()),
        imageUrl: '/demo/1792851254577_laptop.jpg',
        cropUrl: '/demo/crop_backpack.jpg',
        place: 'back of chair',
        verified: -1,
      });
      setSightings(prev => [...prev, s]);
      setActivity(prev => pushActivity(prev, 'new', s, Date.now()));
    }, 6000);
    return () => clearInterval(t);
  }, []);

  const empty = params.get('empty') === '1';
  return (
    <Dashboard
      status={empty ? 'connecting' : 'live'}
      live
      belongings={empty ? [] : belongings}
      sightings={empty ? [] : sightings}
      alerts={empty ? [] : alerts}
      activity={empty ? [] : activity}
      now={now}
      apiBase=""
      dismissing={new Set()}
      onDismissAlert={id => setAlerts(prev => prev.map(a => (a.id === id ? { ...a, dismissed: true } : a)))}
      initialSelectedId={params.get('drawer')}
      ask={{
        entries,
        pending: entries.some(e => e.status === 'pending'),
        onAsk: q => {
          const id = Date.now();
          setEntries(prev => [...prev, { id, question: q, status: 'pending' }]);
          setTimeout(
            () =>
              setEntries(prev =>
                prev.map(e =>
                  e.id === id
                    ? {
                        ...e,
                        status: 'done',
                        response: {
                          answer: 'Your laptop is open on the hacking table in row 3 [#1048].',
                          sightings: [],
                          steps: [{ tool: 'recent_sightings', input: { belonging_id: 'a1f3c9e2b7d4' } }],
                        },
                      }
                    : e
                )
              ),
            1200
          );
        },
        onClear: () => setEntries([]),
      }}
    />
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Demo />
  </StrictMode>
);
