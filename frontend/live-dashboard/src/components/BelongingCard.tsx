import type { Belonging, Sighting } from '../module_bindings/types';
import { useFlash } from '../hooks/useFlash';
import { eventLabel, splitNearby, thumbUrl } from '../lib/sightings';
import { formatClock, formatFull, formatRelative, microsToDate, minutesSince } from '../lib/time';
import { percent } from '../lib/live';
import { HourglassIcon, PinIcon, RecallAvatar } from './Icons';
import { Thumb } from './Thumb';

/** Enrollment collects this many camera angles (backend vision/enroll.py ANGLES). */
export const ENROLL_ANGLES = 5;
/** A "seen" sighting newer than this gets the scan-sweep highlight. */
const FRESH_MS = 5 * 60_000;

type Props = {
  belonging: Belonging;
  latest?: Sighting;
  now: Date;
  apiBase: string;
  /** Enable the "just updated" highlight (false while the initial subscription loads). */
  live: boolean;
  /** Minutes out of view before the card shows the left-behind timer. */
  leftAfter?: number;
  onSelect: (id: string) => void;
};

function EnrollRing({ saved }: { saved: number }) {
  const r = 26;
  const c = 2 * Math.PI * r;
  const done = Math.min(saved, ENROLL_ANGLES) / ENROLL_ANGLES;
  return (
    <span className="ring" aria-hidden="true">
      <svg width="72" height="72" viewBox="0 0 64 64">
        <circle className="ring-orbit" cx="32" cy="32" r="30" fill="none" strokeWidth="1" strokeDasharray="2 5" />
        <g transform="rotate(-90 32 32)">
          <circle className="ring-track" cx="32" cy="32" r={r} strokeWidth="5" />
          <circle
            className="ring-arc"
            cx="32"
            cy="32"
            r={r}
            fill="none"
            strokeWidth="5"
            strokeLinecap="round"
            strokeDasharray={`${done * c} ${c}`}
            style={{ ['--ring-c' as string]: c }}
          />
        </g>
      </svg>
      <span className="ring-label mono">
        {Math.min(saved, ENROLL_ANGLES)}/{ENROLL_ANGLES}
      </span>
    </span>
  );
}

export function BelongingCard({ belonging, latest, now, apiBase, live, leftAfter = 15, onSelect }: Props) {
  const flashKey = latest
    ? `${String(latest.id)}|${latest.description}|${latest.verified}|${latest.event}`
    : '';
  const flash = useFlash(flashKey, live);
  const seenAt = latest ? microsToDate(latest.seenAtMicros) : null;
  const nearby = latest ? splitNearby(latest.nearby) : [];
  const left = latest?.event === 'left_view';
  // Still collecting reference angles (enroll.py): the camera ignores it until done.
  const enrolling = !belonging.tracking;
  const fresh = !!seenAt && !left && now.getTime() - seenAt.getTime() < FRESH_MS;
  const outMins = left && seenAt ? minutesSince(seenAt, now) : 0;
  const stale = left && outMins >= leftAfter;
  const category = belonging.category || 'item';

  const cls = ['card'];
  if (flash) cls.push('card-flash');
  if (!latest) cls.push('card-never');
  if (enrolling) cls.push('card-enrolling');
  if (fresh) cls.push('card-fresh');

  return (
    <article className={cls.join(' ')} data-testid="belonging-card">
      <button
        type="button"
        className="card-hit"
        onClick={() => onSelect(belonging.id)}
        aria-label={`Show sighting history for ${belonging.name}`}
      />
      <div className="card-media scene">
        {latest ? (
          <>
            <Thumb className="card-shot" src={thumbUrl(latest, apiBase)} alt={`${belonging.name}, last sighting`} />
            <span className={`brackets card-brackets${left ? ' brackets-muted' : ''}`} aria-hidden="true" />
            {seenAt && (
              <span className="scene-chip scene-chip-left mono" aria-hidden="true">
                [ {latest.objectName || category}
                {latest.matchScore > 0 ? ` ${percent(latest.matchScore)}` : ''} · {formatClock(seenAt, now)} ]
              </span>
            )}
          </>
        ) : enrolling ? (
          <span className="card-placeholder">
            <EnrollRing saved={belonging.photos} />
            <span className="small">{`${belonging.photos} angle${belonging.photos === 1 ? '' : 's'} saved`}</span>
          </span>
        ) : (
          <span className="card-placeholder">
            <span className="small">Not seen yet</span>
          </span>
        )}
        {fresh && <span className="sweep" aria-hidden="true" />}
        {stale && (
          <span className="stale" title={`Left behind for ${outMins} min`}>
            <span className="stale-icon">
              <HourglassIcon />
            </span>
            <span className="mono" aria-hidden="true">
              {outMins >= 120 ? `${Math.floor(outMins / 60)}h` : `${outMins}m`}
            </span>
            <span className="visually-hidden">Left behind for {outMins} minutes</span>
          </span>
        )}
        {(latest || enrolling) && (
          <div className="badges">
            {enrolling && (
              <span className="badge badge-enrolling" title="Still collecting reference angles">
                Enrolling…
              </span>
            )}
            {latest && (
              <span className={`badge ${left ? 'badge-left' : 'badge-seen'}`}>{eventLabel(latest.event)}</span>
            )}
            {latest?.verified === -1 && (
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
          <span className="chip mono">{category}</span>
        </header>
        {latest && (
          <div className="card-where">
            {latest.place && (
              <p className="place">
                <PinIcon size={20} />
                <span>{latest.place}</span>
              </p>
            )}
            {seenAt && (
              <p className="when mono">
                <time dateTime={seenAt.toISOString()} title={formatFull(seenAt)}>
                  Last seen {formatRelative(seenAt, now)}
                </time>
              </p>
            )}
          </div>
        )}
        <div className="recall">
          <RecallAvatar />
          <div className="bubble">
            <span className="bubble-meta">
              <span>RecallAI</span>
              <span className="mono">{latest?.verified === 1 ? 'verified' : 'recall'}</span>
            </span>
            {latest ? (
              <span className={`bubble-text${latest.description ? '' : ' waiting'}`}>
                {latest.description || 'Waiting for Grok…'}
              </span>
            ) : (
              <span className="bubble-text waiting">
                {enrolling
                  ? 'Hold it in front of the camera from a few more angles to start tracking.'
                  : 'The camera hasn’t spotted this yet.'}
              </span>
            )}
          </div>
        </div>
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
        <footer className="card-foot">
          <span className="mono">
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
