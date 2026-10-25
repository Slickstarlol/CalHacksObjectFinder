import { useEffect, useRef } from 'react';
import { useFocusTrap } from '../hooks/useFocusTrap';
import type { Belonging, Sighting } from '../module_bindings/types';
import { eventLabel, frameUrl, splitNearby, thumbUrl } from '../lib/sightings';
import { formatClock, formatFull, formatRelative, microsToDate } from '../lib/time';
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

export function TimelineDrawer({ belonging, sightings, now, apiBase, onClose, onOpenImage }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const onKeyDown = useFocusTrap(drawerRef);
  useEffect(() => {
    closeRef.current?.focus();
  }, [belonging.id]);

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
            <p className="eyebrow">{belonging.category || 'item'} · sighting history</p>
            <h2 id="drawer-title">{belonging.name}</h2>
          </div>
          <button ref={closeRef} type="button" className="btn btn-quiet" onClick={onClose} aria-label="Close history">
            Close
          </button>
        </header>
        {sightings.length === 0 ? (
          <p className="empty">No sightings yet. Point the camera at your {belonging.name.toLowerCase()}.</p>
        ) : (
          <ol className="timeline">
            {sightings.map(s => {
              const at = microsToDate(s.seenAtMicros);
              const nearby = splitNearby(s.nearby);
              return (
                <li key={String(s.id)} className={`tl-item tl-${s.event === 'left_view' ? 'left' : 'seen'}`}>
                  <Thumb
                    className="tl-thumb"
                    src={thumbUrl(s, apiBase)}
                    alt={`${belonging.name} at ${formatClock(at, now)}`}
                    onClick={() => onOpenImage(frameUrl(s, apiBase), s.description || belonging.name)}
                  />
                  <div className="tl-body">
                    <p className="tl-when">
                      <time dateTime={at.toISOString()} title={formatFull(at)}>
                        {formatClock(at, now)}
                      </time>
                      <span className="muted"> · {formatRelative(at, now)}</span>
                    </p>
                    <p className="tl-tags">
                      <span className={`badge ${s.event === 'left_view' ? 'badge-left' : 'badge-seen'}`}>
                        {eventLabel(s.event)}
                      </span>
                      {s.verified === -1 && <span className="badge badge-unchecked">Unchecked</span>}
                      {s.place && <span className="tag">{s.place}</span>}
                    </p>
                    <p className={`tl-desc${s.description ? '' : ' waiting'}`}>
                      {s.description || 'Waiting for Grok…'}
                    </p>
                    {nearby.length > 0 && <p className="tl-near muted">Near {nearby.join(', ')}</p>}
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
