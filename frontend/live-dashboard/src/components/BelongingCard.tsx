import type { Belonging, Sighting } from '../module_bindings/types';
import { useFlash } from '../hooks/useFlash';
import { eventLabel, splitNearby, thumbUrl } from '../lib/sightings';
import { formatClock, formatFull, formatRelative, microsToDate, minutesSince } from '../lib/time';
import { percent } from '../lib/live';
import { useEffect, useRef, useState } from 'react';
import { HourglassIcon, PencilIcon, PinIcon, RecallAvatar } from './Icons';
import { NameInput } from './NameInput';
import { Thumb } from './Thumb';

/** Enrollment collects this many camera angles (backend vision/enroll.py ANGLES). */
export const ENROLL_ANGLES = 3;
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
  /** Saves a new name (double-click it, or the pencil). Omitted -> read-only. */
  onRename?: (id: string, name: string) => Promise<unknown>;
};

/** A single click on the name waits this long, so a double-click (rename) doesn't also open history. */
const CLICK_DELAY = 250;

/** The card title: click for history, double-click / pencil / Enter / F2 to rename. */
function CardName({
  belonging,
  onSelect,
  onRename,
}: {
  belonging: Belonging;
  onSelect: (id: string) => void;
  onRename?: (id: string, name: string) => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  // Shown until SpacetimeDB mirrors the new name back.
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    if (pending !== null && belonging.name === pending) setPending(null);
  }, [belonging.name, pending]);

  const shown = pending ?? belonging.name;
  if (!onRename) return <h3>{shown}</h3>;

  const save = (name: string | null) => {
    setEditing(false);
    if (!name || name === shown) return;
    setError(null);
    setPending(name);
    onRename(belonging.id, name).catch((err: unknown) => {
      setPending(null);
      setError(err instanceof Error ? err.message : 'Couldn’t save the name.');
    });
  };

  return (
    <div className="card-name">
      {editing ? (
        <NameInput initial={shown} label={`New name for ${shown}`} placeholder="Name this item" onDone={save} />
      ) : (
        <>
          <h3>
            <button
              type="button"
              className="name-btn card-name-btn"
              title="Double-click to rename"
              aria-label={`${shown}. Press Enter or F2 to rename`}
              onClick={e => {
                if (e.detail > 1) return;
                clearTimeout(timer.current);
                timer.current = setTimeout(() => onSelect(belonging.id), CLICK_DELAY);
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
          </h3>
          <button
            type="button"
            className="rename-btn"
            onClick={() => setEditing(true)}
            aria-label={`Rename ${shown}`}
            title="Rename"
          >
            <PencilIcon />
          </button>
        </>
      )}
      {error && (
        <span className="card-name-error small" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

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

export function BelongingCard({ belonging, latest, now, apiBase, live, leftAfter = 15, onSelect, onRename }: Props) {
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
              <span className="badge badge-unchecked" title="Khris hasn’t verified this match yet">
                Unchecked
              </span>
            )}
          </div>
        )}
      </div>
      <div className="card-body">
        <header className="card-head">
          <CardName belonging={belonging} onSelect={onSelect} onRename={onRename} />
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
                {latest.description || 'Waiting for Khris…'}
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
