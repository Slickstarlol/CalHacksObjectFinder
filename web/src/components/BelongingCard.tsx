import type { Belonging, Sighting } from '../module_bindings/types';
import { useFlash } from '../hooks/useFlash';
import { eventLabel, splitNearby, thumbUrl } from '../lib/sightings';
import { formatFull, formatRelative, microsToDate } from '../lib/time';
import { ClockIcon, PinIcon, Thumb } from './Thumb';

type Props = {
  belonging: Belonging;
  latest?: Sighting;
  now: Date;
  apiBase: string;
  /** Enable the "just updated" highlight (false while the initial subscription loads). */
  live: boolean;
  onSelect: (id: string) => void;
};

export function BelongingCard({ belonging, latest, now, apiBase, live, onSelect }: Props) {
  const flashKey = latest
    ? `${String(latest.id)}|${latest.description}|${latest.verified}|${latest.event}`
    : '';
  const flash = useFlash(flashKey, live);
  const seenAt = latest ? microsToDate(latest.seenAtMicros) : null;
  const nearby = latest ? splitNearby(latest.nearby) : [];
  const left = latest?.event === 'left_view';

  return (
    <article className={`card${flash ? ' card-flash' : ''}${latest ? '' : ' card-never'}`} data-testid="belonging-card">
      <button
        type="button"
        className="card-hit"
        onClick={() => onSelect(belonging.id)}
        aria-label={`Show sighting history for ${belonging.name}`}
      />
      <div className="card-media">
        {latest ? (
          <Thumb src={thumbUrl(latest, apiBase)} alt={`${belonging.name}, last sighting`} />
        ) : (
          <span className="thumb thumb-never">
            <span>Not seen yet</span>
          </span>
        )}
        {latest && (
          <div className="badges">
            <span className={`badge ${left ? 'badge-left' : 'badge-seen'}`}>{eventLabel(latest.event)}</span>
            {latest.verified === -1 && (
              <span className="badge badge-unchecked" title="Grok hasn’t verified this match yet">
                Unchecked
              </span>
            )}
          </div>
        )}
      </div>
      <div className="card-body">
        <header className="card-head">
          <h3>{belonging.name}</h3>
          <span className="chip">{belonging.category || 'item'}</span>
        </header>
        {latest ? (
          <>
            <p className={`card-desc${latest.description ? '' : ' waiting'}`}>
              {latest.description || 'Waiting for Grok…'}
            </p>
            <ul className="meta">
              {seenAt && (
                <li>
                  <ClockIcon />
                  <time dateTime={seenAt.toISOString()} title={formatFull(seenAt)}>
                    Last seen {formatRelative(seenAt, now)}
                  </time>
                </li>
              )}
              {latest.place && (
                <li>
                  <PinIcon />
                  <span>{latest.place}</span>
                </li>
              )}
            </ul>
            {nearby.length > 0 && (
              <p className="nearby">
                <span className="nearby-label">Near</span>
                {nearby.map(n => (
                  <span key={n} className="tag">
                    {n}
                  </span>
                ))}
              </p>
            )}
          </>
        ) : (
          <p className="card-desc waiting">The camera hasn’t spotted this yet.</p>
        )}
        <footer className="card-foot">
          <span>
            {belonging.photos} reference photo{belonging.photos === 1 ? '' : 's'}
          </span>
          <span className="card-more" aria-hidden="true">
            History →
          </span>
        </footer>
      </div>
    </article>
  );
}
