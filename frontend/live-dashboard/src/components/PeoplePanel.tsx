import { useEffect, useMemo, useState } from 'react';
import type { Person } from '../module_bindings/types';
import { knownFor, sortPeople } from '../lib/people';
import { NameInput } from './NameInput';

type Props = {
  people: readonly Person[];
  now: Date;
  /** Saves a new name (PUT /api/people/{id}). Omitted -> names are read-only. */
  onRename?: (person: Person, name: string) => Promise<void>;
};

const TONES = ['tone-sky', 'tone-sand', 'tone-mint'];

/** A person's name: double-click (or Enter / F2) to rename it in place. */
function PersonName({ shown, onSave }: { shown: string; onSave?: (name: string) => void }) {
  const [editing, setEditing] = useState(false);

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
      onDoubleClick={() => setEditing(true)}
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

/** People saved in RecallAI (added from the camera dashboard / POST /api/people). */
export function PeoplePanel({ people, now, onRename }: Props) {
  const sorted = useMemo(() => sortPeople(people), [people]);
  // Names saved but not yet mirrored back by SpacetimeDB, keyed by person id.
  const [pending, setPending] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

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
            const known = knownFor(p.knownSince, now);
            const shown = pending[String(p.id)] ?? p.name;
            return (
              <li key={String(p.id)} className="person" data-testid="person">
                <span className={`person-tile ${TONES[i % TONES.length]}`} aria-hidden="true">
                  {shown.charAt(0).toUpperCase()}
                </span>
                <div className="person-body">
                  <div className="person-head">
                    <PersonName shown={shown} onSave={onRename ? save(p) : undefined} />
                    {p.relationship && <span className="chip">{p.relationship}</span>}
                  </div>
                  {known && <span className="muted small">Known for {known}</span>}
                  {p.description && <p className="person-desc">{p.description}</p>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
