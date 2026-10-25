import { activityText, type ActivityItem } from '../lib/activity';
import { formatShortAgo } from '../lib/time';

type Props = { items: ActivityItem[]; now: Date; apiBase?: string; onSelect: (belongingId: string) => void };

function tone(item: ActivityItem): string {
  if (item.kind === 'described') return 'dot-accent';
  if (item.kind === 'updated') return 'dot-muted';
  return item.sighting.event === 'left_view' ? 'dot-warn' : 'dot-live';
}

/** Changes streamed from SpacetimeDB since this page opened (newest first). */
export function ActivityFeed({ items, now, onSelect }: Props) {
  return (
    <section className="panel activity" aria-labelledby="activity-title">
      <header className="panel-head">
        <h2 id="activity-title">Live activity</h2>
        <span className="mono muted small">since you opened this</span>
      </header>
      {items.length === 0 ? (
        <p className="muted small">New sightings will appear here as the camera spots them.</p>
      ) : (
        <ul className="feed">
          {items.map((item, i) => (
            <li key={item.key} className={i === 0 ? 'feed-new' : undefined}>
              <button type="button" className="feed-row" onClick={() => onSelect(item.sighting.belongingId)}>
                <span className={`dot ${tone(item)}`} aria-hidden="true" />
                <span className="feed-text">
                  <span className="feed-title">{activityText(item)}</span>
                  <span className="feed-sub">
                    {item.sighting.place || item.sighting.description || item.sighting.objectName}
                  </span>
                </span>
                <time className="mono muted small">{formatShortAgo(new Date(item.receivedAt), now)}</time>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
