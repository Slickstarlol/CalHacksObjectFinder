import { useEffect, useRef } from 'react';
import { useFocusTrap } from '../hooks/useFocusTrap';
import type { Belonging, Sighting } from '../module_bindings/types';
import { eventLabel, frameUrl, splitNearby, thumbUrl } from '../lib/sightings';
import { formatClock, formatFull, formatRelative, microsToDate } from '../lib/time';
import { CloseIcon, PinIcon, RecallAvatar } from './Icons';
import { Thumb } from './Thumb';
import type { OpenImage } from './types';

type Props = {
  belonging: Belonging;
  sightings: Sighting[];
  now: Date;
  apiBase: string;
  onClose: () => void;
  onOpenImage: OpenImage;
};

/** Grok's description of the latest sighting, plus how it got there, in plain sentences. */
function recallText(belonging: Belonging, timeline: Sighting[], now: Date): string[] {
  const [latest, previous] = timeline;
  if (!latest) {
    return belonging.tracking
      ? ['Not recalled yet: the camera hasn’t spotted it.']
      : ['Not recalled yet.', 'RecallAI needs a few more reference photos from different angles before it can start tracking this.'];
  }
  const out = [latest.description || 'Grok hasn’t described this sighting yet.'];
  const at = formatClock(microsToDate(latest.seenAtMicros), now);
  if (latest.event === 'left_view') out.push(`It left the camera’s view at ${at} and hasn’t been seen since.`);
  if (previous?.place && previous.place !== latest.place && latest.place) {
    out.push(`Before that it was at ${previous.place}.`);
  }
  if (latest.verified === -1) out.push('This match hasn’t been checked by Grok yet, so it could be a similar-looking item.');
  return out;
}

export function TimelineDrawer({ belonging, sightings, now, apiBase, onClose, onOpenImage }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const onKeyDown = useFocusTrap(drawerRef);
  useEffect(() => {
    closeRef.current?.focus();
  }, [belonging.id]);

  const latest = sightings[0];
  const latestAt = latest ? microsToDate(latest.seenAtMicros) : null;
  const near = latest ? splitNearby(latest.nearby) : [];

  return (
    <div className="drawer-layer" onClick={onClose}>
      <aside
        ref={drawerRef}
        onKeyDown={onKeyDown}
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
        onClick={e => e.stopPropagation()}
      >
        <header className="drawer-head">
          <div>
            <p className="eyebrow mono">{belonging.category || 'item'} · sighting history</p>
            <h2 id="drawer-title">{belonging.name}</h2>
          </div>
          <button ref={closeRef} type="button" className="btn-icon" onClick={onClose} aria-label="Close history">
            <CloseIcon />
          </button>
        </header>

        <section className="drawer-recall" aria-labelledby="recall-title">
          <h3 id="recall-title" className="eyebrow mono">
            Where it’s recalled
          </h3>
          {latest?.place && latestAt && (
            <p className="place place-lg">
              <PinIcon size={20} />
              <span>{latest.place}</span>
              <time className="mono muted small" dateTime={latestAt.toISOString()}>
                {formatRelative(latestAt, now)}
              </time>
            </p>
          )}
          <div className="recall">
            <RecallAvatar size={30} />
            <div className="bubble bubble-lg pop">
              <span className="bubble-meta">
                <span>RecallAI</span>
                <span className="mono">full recall</span>
              </span>
              {recallText(belonging, sightings, now).map((p, i) => (
                <p key={i}>{p}</p>
              ))}
            </div>
          </div>
          {near.length > 0 && (
            <p className="nearby nearby-indent">
              <span className="nearby-label">Near</span>
              {near.map(n => (
                <span key={n} className="tag">
                  {n}
                </span>
              ))}
            </p>
          )}
        </section>

        <h3 className="eyebrow mono drawer-sub">Sightings</h3>
        {sightings.length === 0 ? (
          <p className="empty muted">No sightings yet. Point the camera at your {belonging.name.toLowerCase()}.</p>
        ) : (
          <ol className="timeline">
            {sightings.map(s => {
              const at = microsToDate(s.seenAtMicros);
              const nearby = splitNearby(s.nearby);
              const isLeft = s.event === 'left_view';
              return (
                <li key={String(s.id)} className={`tl-item tl-${isLeft ? 'left' : 'seen'}`}>
                  <Thumb
                    className="tl-thumb"
                    src={thumbUrl(s, apiBase)}
                    alt={`${belonging.name} at ${formatClock(at, now)}`}
                    onClick={() => onOpenImage(frameUrl(s, apiBase), s.description || belonging.name)}
                  />
                  <div className="tl-body">
                    <p className="tl-when">
                      <time className="mono" dateTime={at.toISOString()} title={formatFull(at)}>
                        {formatClock(at, now)}
                      </time>
                      <span className="muted"> · {formatRelative(at, now)}</span>
                    </p>
                    <p className={`tl-desc${s.description ? '' : ' waiting'}`}>{s.description || 'Waiting for Grok…'}</p>
                    <p className="tl-tags">
                      <span className={`badge ${isLeft ? 'badge-left' : 'badge-seen'}`}>{eventLabel(s.event)}</span>
                      {s.verified === -1 && <span className="badge badge-unchecked">Unchecked</span>}
                      {s.place && <span className="muted small">{s.place}</span>}
                    </p>
                    {nearby.length > 0 && <p className="tl-near muted small">Near {nearby.join(', ')}</p>}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </aside>
    </div>
  );
}
