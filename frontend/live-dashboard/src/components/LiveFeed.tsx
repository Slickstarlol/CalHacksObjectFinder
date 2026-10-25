import { useEffect, useState } from 'react';
import type { Belonging, Sighting } from '../module_bindings/types';
import {
  accuracyTone,
  boxPercent,
  feedAccuracy,
  otherDetections,
  percent,
  type CameraState,
} from '../lib/live';
import { formatClock, formatShortAgo, microsToDate } from '../lib/time';
import { CameraIcon } from './Icons';

type Props = {
  camera?: CameraState;
  /** Belongings whose latest sighting is "left view" (shown as Missing). */
  missing: { belonging: Belonging; sighting: Sighting }[];
  now: Date;
  onSelect: (belongingId: string) => void;
};

type Row = {
  key: string;
  kind: 'in-view' | 'person' | 'object' | 'missing';
  label: string;
  title: string;
  sub: string;
  /** 0..100, or null when there's no score (e.g. out of view). */
  acc: number | null;
  at: string;
  belongingId?: string;
};

const DOCK_TONES = ['tone-sky', 'tone-sand', 'tone-mint'];

/** Ticking wall clock (its own 1 s timer so the dashboard doesn't re-render every second). */
export function LiveClock({ weekday = false }: { weekday?: boolean }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const time = now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  const day = weekday ? `${now.toLocaleDateString(undefined, { weekday: 'short' })} ` : '';
  return (
    <time dateTime={now.toISOString()} suppressHydrationWarning>
      {day}
      {time}
    </time>
  );
}

function AccuracyMeter({ pct, width = 40 }: { pct: number | null; width?: number }) {
  return (
    <span className="meter" style={{ width }} aria-hidden="true">
      <span className={`meter-fill tone-${accuracyTone(pct)}`} style={{ width: `${pct ?? 0}%` }} />
    </span>
  );
}

export function LiveFeed({ camera, missing, now, onSelect }: Props) {
  const status = camera?.status ?? 'offline';
  const online = status === 'online';
  const inView = online ? camera!.inView : [];
  const others = online ? otherDetections(camera!.detections, inView) : [];
  const people = others.find(g => g.category === 'person');
  const objects = others.filter(g => g.category !== 'person');
  const acc = feedAccuracy(inView);
  const w = camera?.width ?? null;
  const h = camera?.height ?? null;

  const rows: Row[] = [
    ...inView.map(i => ({
      key: `v-${i.belongingId}`,
      kind: 'in-view' as const,
      label: 'In view',
      title: i.name,
      sub: `${i.category || 'item'} · matched to your reference photos`,
      acc: i.score * 100,
      at: 'now',
      belongingId: i.belongingId,
    })),
    ...missing.map(({ belonging, sighting }) => {
      const at = microsToDate(sighting.seenAtMicros);
      return {
        key: `m-${belonging.id}`,
        kind: 'missing' as const,
        label: 'Missing',
        title: belonging.name,
        sub: `Out of view since ${formatClock(at, now)}${sighting.place ? ` · ${sighting.place}` : ''}`,
        acc: null,
        at: formatShortAgo(at, now),
        belongingId: belonging.id,
      };
    }),
    ...(people
      ? [{
          key: 'person',
          kind: 'person' as const,
          label: 'Person',
          title: `${people.count} ${people.count === 1 ? 'person' : 'people'} in view`,
          sub: 'Detected, not identified',
          acc: people.confidence * 100,
          at: 'now',
        }]
      : []),
    ...objects.map(g => ({
      key: `o-${g.category}`,
      kind: 'object' as const,
      label: 'Object',
      title: g.count > 1 ? `${g.count} × ${g.category}` : g.category,
      sub: 'Not one of your belongings',
      acc: g.confidence * 100,
      at: 'now',
    })),
  ].slice(0, 6);

  const alt = inView.length
    ? `Live camera. Yours in view: ${inView.map(i => i.name).join(', ')}`
    : 'Live camera from the RecallAI vision loop';
  const tracked = camera?.detections.length ?? 0;

  return (
    <section className="panel live" aria-labelledby="live-title">
      <div className="live-main">
        <header className="live-head">
          <h2 id="live-title">
            Live feed
            <span className={`rec${online ? ' rec-on' : ''}`}>
              <span className="rec-dot" aria-hidden="true" />
              {online ? 'REC' : 'OFF'}
            </span>
          </h2>
          <span className="mono muted small live-sub">
            <LiveClock />
          </span>
        </header>
        <div className="scene live-scene" style={w && h ? { aspectRatio: `${w} / ${h}` } : undefined}>
          {online && camera!.frameUrl ? (
            <img className="live-frame" src={camera!.frameUrl} alt={alt} />
          ) : (
            <div className="live-empty">
              <CameraIcon />
              {status === 'connecting' || (online && !camera!.frameUrl) ? (
                <p>Connecting to the camera…</p>
              ) : status === 'unreachable' ? (
                <p>Can’t reach the RecallAI server on port 8000.</p>
              ) : (
                <>
                  <p>The camera is off. Start the vision loop and this fills in.</p>
                  <code>python -m recallai.vision.webcam</code>
                </>
              )}
            </div>
          )}
          {online &&
            inView.map((i, n) => {
              const pos = boxPercent(i.bbox, w, h);
              if (!pos) return null;
              return (
                <button
                  key={`${i.belongingId}-${n}`}
                  type="button"
                  className="lock"
                  style={{ left: `${pos.left}%`, top: `${pos.top}%`, width: `${pos.width}%`, height: `${pos.height}%` }}
                  onClick={() => i.belongingId && onSelect(i.belongingId)}
                  aria-label={`${i.name} in view, ${percent(i.score)} match. Open its history`}
                >
                  <span className="brackets" aria-hidden="true" />
                  <span className="lock-tag mono" aria-hidden="true">
                    {i.name} {percent(i.score)}
                  </span>
                </button>
              );
            })}
          {online && (
            <>
              <span className="scene-chip scene-chip-left mono">
                Python camera{camera!.gps ? ' · GPS on' : ''}
              </span>
              <span className="scene-chip scene-chip-right mono">
                {tracked} tracked{people ? ` · ${people.count} ${people.count === 1 ? 'person' : 'people'}` : ''}
              </span>
            </>
          )}
        </div>
      </div>

      <div className="live-side">
        <h3 className="live-side-title">Yours in view</h3>
        {inView.length === 0 ? (
          <p className="muted small dock-empty">
            {online ? 'None of your belongings are in view right now.' : 'Nothing in view while the camera is off.'}
          </p>
        ) : (
          <ul className="dock" aria-label="Your belongings in view">
            {inView.map((i, n) => (
              <li key={`${i.belongingId}-${n}`} className="dock-item">
                <button
                  type="button"
                  onClick={() => i.belongingId && onSelect(i.belongingId)}
                  aria-label={`Open history for ${i.name} (${percent(i.score)} match)`}
                >
                  <span className={`dock-tile ${DOCK_TONES[n % DOCK_TONES.length]}`} aria-hidden="true">
                    {i.name.replace(/^my\s+/i, '').charAt(0).toUpperCase()}
                  </span>
                  <span className="dock-name" aria-hidden="true">
                    {i.name} <span className="mono tone-text-good">{percent(i.score)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        <header className="live-side-head">
          <h3 className="live-side-title">What’s happening</h3>
          <span
            className={`acc-pill mono acc-${accuracyTone(acc)}`}
            title="Average match score of your belongings in the current frame"
          >
            <AccuracyMeter pct={acc} width={34} />
            match {acc === null ? '—' : `${acc.toFixed(1)}%`}
          </span>
        </header>
        {rows.length === 0 ? (
          <p className="muted small">Nothing to report. Items appear here as the camera sees them.</p>
        ) : (
          <ol className="events">
            {rows.map(r => {
              const body = (
                <>
                  <span className={`kind kind-${r.kind} mono`}>{r.label}</span>
                  <span className="event-text">
                    <span className="event-title">{r.title}</span>
                    <span className="event-sub">{r.sub}</span>
                  </span>
                  <span className="event-acc" title="Detection score">
                    <span className={`mono acc-num acc-${accuracyTone(r.acc)}`}>
                      {r.acc === null ? '—' : `${r.acc.toFixed(1)}%`}
                    </span>
                    <AccuracyMeter pct={r.acc} />
                  </span>
                  <span className="mono event-at">{r.at}</span>
                </>
              );
              return (
                <li key={r.key} className="event">
                  {r.belongingId ? (
                    <button type="button" className="event-row" onClick={() => onSelect(r.belongingId!)}>
                      {body}
                    </button>
                  ) : (
                    <div className="event-row">{body}</div>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </section>
  );
}
