/**
 * Dev-only demo (http://localhost:5174/demo.html): renders the presentational
 * Dashboard with fixture data, no SpacetimeDB or FastAPI needed.
 * Query params: ?theme=dark|light  ?drawer=<belongingId>  ?simulate=1  ?empty=1  ?camera=off  ?label=fail
 */
import { StrictMode, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Dashboard } from '../components/Dashboard';
import type { AskEntry } from '../components/types';
import { pushActivity, type ActivityItem } from '../lib/activity';
import { useNow } from '../hooks/useNow';
import { parseLive, type CameraState } from '../lib/live';
import { applyStoredTheme } from '../lib/theme';
import type { Sighting } from '../module_bindings/types';
import {
  askEntries,
  demoLive,
  belongings,
  enrollingBelonging,
  makeActivity,
  makePeople,
  makeAlerts,
  makeSighting,
  makeSightings,
  micros,
} from './fixtures';
import '../styles.css';

const params = new URLSearchParams(location.search);
const initialCamera: CameraState =
  params.get('camera') === 'off'
    ? { ...parseLive({ online: false }), status: 'offline', frameUrl: '' }
    : { ...parseLive(demoLive), status: 'online', frameUrl: '/demo/1792851254577_laptop.jpg' };
const wait = (ms: number) => new Promise(r => setTimeout(r, ms));
const theme = params.get('theme');
if (theme === 'dark' || theme === 'light') document.documentElement.dataset.theme = theme;
else applyStoredTheme();

function Demo() {
  const start = useMemo(() => Date.now(), []);
  const [sightings, setSightings] = useState<Sighting[]>(() => makeSightings(start));
  const [alerts, setAlerts] = useState(() => makeAlerts(start));
  const [people, setPeople] = useState(() => makePeople(start));
  const [camera, setCamera] = useState(initialCamera);
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
      belongings={empty ? [] : [...belongings, enrollingBelonging]}
      people={empty ? [] : people}
      onRenamePerson={async (person, name) => {
        // Stand-in for PUT /api/people/{id} + the bridge echoing the row back.
        await wait(600);
        setPeople(prev => prev.map(p => (p.id === person.id ? { ...p, name } : p)));
        setCamera(prev => ({
          ...prev,
          people: prev.people.map(p => (p.personId === Number(person.id) ? { ...p, name } : p)),
        }));
      }}
      onDescribePerson={async (person, refresh) => {
        // Stand-in for POST /api/people/{id}/describe (Grok on the camera's photos).
        await wait(refresh ? 1600 : 1100);
        if (person.id !== 1n) throw new Error(`No photos of ${person.name} yet. Name them on the live feed and the camera saves some.`);
        return {
          summary: refresh ? 'Dark hoodie, glasses' : 'Black hoodie, round glasses',
          description:
            'Black CalHacks hoodie with the hood down, round dark-framed glasses and short dark hair. Usually leaning over a laptop at the long table.',
          samePerson: 'yes',
          photos: ['/demo/person_chawin_1.jpg', '/demo/person_chawin_2.jpg'],
          savedToNotes: false,
        };
      }}
      onLabelPerson={async (bbox, name, personId) => {
        // Stand-in for POST /api/live/people, then the camera recognizing them.
        await wait(400);
        if (params.get('label') === 'fail') throw new Error('That person isn’t in view any more.');
        let person =
          personId !== undefined
            ? people.find(p => Number(p.id) === personId)
            : people.find(p => p.name.toLowerCase() === name.toLowerCase());
        const created = !person;
        if (!person) {
          const t = micros(Date.now());
          person = { id: BigInt(Date.now()), name, description: '', relationship: '', knownSince: '', createdAtMicros: t, updatedAtMicros: t, owner: '1' };
          const added = person;
          setPeople(prev => [...prev, added]);
        }
        const id = Number(person.id);
        const shown = person.name;
        setTimeout(() => {
          setCamera(prev => ({
            ...prev,
            people: [...prev.people.filter(p => p.personId !== id), { personId: id, name: shown, score: 0.79, bbox }],
          }));
        }, 1800);
        return { personId: id, name: shown, created, bbox };
      }}
      sightings={empty ? [] : sightings}
      alerts={empty ? [] : alerts}
      activity={empty ? [] : activity}
      now={now}
      apiBase=""
      dismissing={new Set()}
      onDismissAlert={id => setAlerts(prev => prev.map(a => (a.id === id ? { ...a, dismissed: true } : a)))}
      camera={empty ? undefined : camera}
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
