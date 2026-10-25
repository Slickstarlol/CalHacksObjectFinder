import { useMemo } from 'react';
import type { Person } from '../module_bindings/types';
import { knownFor, sortPeople } from '../lib/people';

type Props = { people: readonly Person[]; now: Date };

const TONES = ['tone-sky', 'tone-sand', 'tone-mint'];

/** People saved in RecallAI (added from the camera dashboard / POST /api/people). */
export function PeoplePanel({ people, now }: Props) {
  const sorted = useMemo(() => sortPeople(people), [people]);
  return (
    <section className="panel people" aria-labelledby="people-title">
      <header className="panel-head">
        <h2 id="people-title">People</h2>
        {sorted.length > 0 && <span className="mono muted small">{sorted.length}</span>}
      </header>
      {sorted.length === 0 ? (
        <p className="muted small">No people saved yet.</p>
      ) : (
        <ul className="people-list">
          {sorted.map((p, i) => {
            const known = knownFor(p.knownSince, now);
            return (
              <li key={String(p.id)} className="person" data-testid="person">
                <span className={`person-tile ${TONES[i % TONES.length]}`} aria-hidden="true">
                  {p.name.charAt(0).toUpperCase()}
                </span>
                <div className="person-body">
                  <div className="person-head">
                    <span className="person-name">{p.name}</span>
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
