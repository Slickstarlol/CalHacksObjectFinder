import type { Alert, Sighting } from '../module_bindings/types';
import { frameUrl, thumbUrl } from '../lib/sightings';
import { formatFull, formatRelative, microsToDate } from '../lib/time';
import { Thumb } from './Thumb';
import type { OpenImage } from './types';

type Props = {
  alerts: Alert[];
  sightingsById: Map<string, Sighting>;
  now: Date;
  apiBase: string;
  dismissing: Set<string>;
  onDismiss: (id: bigint) => void;
  onOpenImage: OpenImage;
};

export function AlertsBanner({ alerts, sightingsById, now, apiBase, dismissing, onDismiss, onOpenImage }: Props) {
  if (alerts.length === 0) return null;
  return (
    <section className="alerts" aria-label="Alerts" aria-live="polite">
      {alerts.map(a => {
        const s = a.sightingId ? sightingsById.get(String(a.sightingId)) : undefined;
        const created = microsToDate(a.createdAtMicros);
        const high = a.urgency === 'high';
        const key = String(a.id);
        return (
          <article key={key} className={`alert ${high ? 'alert-high' : 'alert-low'}`}>
            {s && (
              <Thumb
                className="alert-thumb"
                src={thumbUrl(s, apiBase)}
                alt={`Last sighting of ${a.belongingName}`}
                onClick={() => onOpenImage(frameUrl(s, apiBase), s.description || a.belongingName)}
              />
            )}
            <div className="alert-body">
              <p className="alert-kicker">
                <span className="alert-urgency">{high ? 'Don’t forget' : 'Heads up'}</span>
                <span className="dot-sep" aria-hidden="true">·</span>
                <time dateTime={created.toISOString()} title={formatFull(created)}>
                  {formatRelative(created, now)}
                </time>
              </p>
              <p className="alert-message">{a.message || `Did you forget your ${a.belongingName}?`}</p>
            </div>
            <button
              type="button"
              className="btn btn-quiet alert-dismiss"
              onClick={() => onDismiss(a.id)}
              disabled={dismissing.has(key)}
            >
              Dismiss
            </button>
          </article>
        );
      })}
    </section>
  );
}
