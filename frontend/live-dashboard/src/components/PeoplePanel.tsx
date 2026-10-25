import { useEffect, useMemo, useRef, useState } from 'react';
import type { Person } from '../module_bindings/types';
import { knownFor, sortPeople, type PersonDescription } from '../lib/people';
import { resolveUrl } from '../lib/url';
import { SparkIcon } from './Icons';
import { NameInput } from './NameInput';

type Props = {
  people: readonly Person[];
  now: Date;
  /** Saves a new name (PUT /api/people/{id}). Omitted -> names are read-only. */
  onRename?: (person: Person, name: string) => Promise<void>;
  /** Grok describes a person from the camera's photos (click them). Omitted -> rows don't open. */
  onDescribe?: (person: Person, refresh: boolean) => Promise<PersonDescription>;
  apiBase?: string;
};

type GrokState = { status: 'loading' | 'done' | 'error'; data?: PersonDescription; error?: string };

const TONES = ['tone-sky', 'tone-sand', 'tone-mint'];
/** A single click on a name waits this long, so a double-click (rename) doesn't also open the row. */
const CLICK_DELAY = 250;

/** A person's name: double-click (or Enter / F2) to rename it in place. */
function PersonName({ shown, onSave, onOpen }: { shown: string; onSave?: (name: string) => void; onOpen?: () => void }) {
  const [editing, setEditing] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);

  if (!onSave) return <span className="person-name">{shown}</span>;
  if (editing) {
    return (
      <NameInput
        initial={shown}
        label={`New name for ${shown}`}
        onDone={name => {
          setEditing(false);
          if (name && name !== shown) onSave(name);
        }}
      />
    );
  }
  return (
    <button
      type="button"
      className="person-name name-btn"
      title="Double-click to rename"
      aria-label={`${shown}, press Enter to rename`}
      onClick={e => {
        if (!onOpen || e.detail > 1) return;
        clearTimeout(timer.current);
        timer.current = setTimeout(onOpen, CLICK_DELAY);
      }}
      onDoubleClick={() => {
        clearTimeout(timer.current);
        setEditing(true);
      }}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === 'F2') {
          e.preventDefault();
          setEditing(true);
        }
      }}
    >
      {shown}
    </button>
  );
}

/** What Grok sees in a person's photos. */
function PersonGrok({
  id,
  name,
  state,
  apiBase,
  onAgain,
}: {
  id: string;
  name: string;
  state?: GrokState;
  apiBase: string;
  onAgain: () => void;
}) {
  const data = state?.data;
  return (
    <div className="person-grok" id={id} aria-live="polite">
      {data && data.photos.length > 0 && (
        <div className="person-photos">
          {data.photos.map(src => (
            <img key={src} src={resolveUrl(src, apiBase)} alt="" loading="lazy" />
          ))}
        </div>
      )}
      <div className="grok-bubble">
        <span className="grok-who mono">
          <SparkIcon /> Grok
        </span>
        {state?.status === 'loading' ? (
          <p className="grok-thinking">
            <span className="typing" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            Looking at {name}’s photos…
          </p>
        ) : state?.status === 'error' ? (
          <p className="grok-error" role="alert">
            {state.error}
          </p>
        ) : data ? (
          <>
            {data.summary && <p className="grok-summary">{data.summary}</p>}
            <p className="grok-desc">{data.description}</p>
            {data.samePerson === 'no' && (
              <p className="grok-warn">
                Some of these photos may show someone else. Name {name} again on the live feed to add better ones.
              </p>
            )}
            {data.savedToNotes && <p className="grok-note">Saved to {name}’s notes.</p>}
          </>
        ) : null}
        {state && state.status !== 'loading' && (
          <button type="button" className="btn-link grok-again" onClick={onAgain}>
            Ask again
          </button>
        )}
      </div>
    </div>
  );
}

/** People saved in RecallAI. Click one for Grok's description of them. */
export function PeoplePanel({ people, now, onRename, onDescribe, apiBase = '' }: Props) {
  const sorted = useMemo(() => sortPeople(people), [people]);
  // Names saved but not yet mirrored back by SpacetimeDB, keyed by person id.
  const [pending, setPending] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [grok, setGrok] = useState<Record<string, GrokState>>({});

  useEffect(() => {
    setPending(prev => {
      const next = { ...prev };
      let changed = false;
      for (const p of people) {
        const key = String(p.id);
        if (next[key] !== undefined && next[key] === p.name) {
          delete next[key];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [people]);

  const save = (person: Person) => (name: string) => {
    const key = String(person.id);
    setError(null);
    setPending(prev => ({ ...prev, [key]: name }));
    onRename!(person, name).catch((err: unknown) => {
      setPending(({ [key]: _gone, ...rest }) => rest);
      setError(err instanceof Error ? err.message : 'Couldn’t save the name.');
    });
  };

  const describe = (person: Person, refresh: boolean) => {
    const key = String(person.id);
    setGrok(g => ({ ...g, [key]: { ...g[key], status: 'loading', error: undefined } }));
    onDescribe!(person, refresh).then(
      data => setGrok(g => ({ ...g, [key]: { status: 'done', data } })),
      (err: unknown) =>
        setGrok(g => ({
          ...g,
          [key]: { status: 'error', data: g[key]?.data, error: err instanceof Error ? err.message : 'Grok couldn’t describe them.' },
        }))
    );
  };

  const toggle = (person: Person) => {
    const key = String(person.id);
    if (openId === key) {
      setOpenId(null);
      return;
    }
    setOpenId(key);
    if (!grok[key] || grok[key].status === 'error') describe(person, false);
  };

  return (
    <section className="panel people" aria-labelledby="people-title">
      <header className="panel-head">
        <h2 id="people-title">People</h2>
        {sorted.length > 0 && <span className="mono muted small">{sorted.length}</span>}
      </header>
      {error && (
        <p className="notice notice-error small" role="alert">
          {error}
        </p>
      )}
      {sorted.length === 0 ? (
        <p className="muted small">No people saved yet.</p>
      ) : (
        <ul className="people-list">
          {sorted.map((p, i) => {
            const key = String(p.id);
            const known = knownFor(p.knownSince, now);
            const shown = pending[key] ?? p.name;
            const open = openId === key;
            const tile = `person-tile ${TONES[i % TONES.length]}`;
            return (
              <li
                key={key}
                className={`person${onDescribe ? ' is-clickable' : ''}${open ? ' is-open' : ''}`}
                data-testid="person"
                onClick={
                  onDescribe
                    ? e => {
                        // The tile, name and Grok's answer handle their own clicks.
                        if (!(e.target as HTMLElement).closest('button, input, a, .person-grok')) toggle(p);
                      }
                    : undefined
                }
              >
                {onDescribe ? (
                  <button
                    type="button"
                    className={tile}
                    aria-expanded={open}
                    aria-controls={`grok-${key}`}
                    aria-label={`${open ? 'Hide' : 'Show'} what Grok sees in ${shown}’s photos`}
                    onClick={() => toggle(p)}
                  >
                    {shown.charAt(0).toUpperCase()}
                  </button>
                ) : (
                  <span className={tile} aria-hidden="true">
                    {shown.charAt(0).toUpperCase()}
                  </span>
                )}
                <div className="person-body">
                  <div className="person-head">
                    <PersonName
                      shown={shown}
                      onSave={onRename ? save(p) : undefined}
                      onOpen={onDescribe ? () => toggle(p) : undefined}
                    />
                    {p.relationship && <span className="chip">{p.relationship}</span>}
                    {onDescribe && (
                      <span className="person-spark" title="Click for Grok’s description" aria-hidden="true">
                        <SparkIcon />
                      </span>
                    )}
                  </div>
                  {known && <span className="muted small">Known for {known}</span>}
                  {p.description && <p className="person-desc">{p.description}</p>}
                  {open && onDescribe && (
                    <PersonGrok
                      id={`grok-${key}`}
                      name={shown}
                      state={grok[key]}
                      apiBase={apiBase}
                      onAgain={() => describe(p, true)}
                    />
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
