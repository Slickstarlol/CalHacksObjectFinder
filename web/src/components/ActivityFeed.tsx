import { activityText, type ActivityItem } from '../lib/activity';
import { thumbUrl } from '../lib/sightings';
import { formatRelative } from '../lib/time';
import { Thumb } from './Thumb';

type Props = { items: ActivityItem[]; now: Date; apiBase: string; onSelect: (belongingId: string) => void };

export function ActivityFeed({ items, now, apiBase, onSelect }: Props) {
  return (
    <section className="panel activity" aria-labelledby="activity-title">
      <header className="panel-head">
        <h2 id="activity-title">Live activity</h2>
      </header>
      {items.length === 0 ? (
        <p className="muted small">New sightings will appear here as the camera spots them.</p>
      ) : (
        <ul className="feed">
          {items.map(item => (
            <li key={item.key} className={`feed-item feed-${item.kind}`}>
              <button type="button" onClick={() => onSelect(item.sighting.belongingId)}>
                <Thumb className="feed-thumb" src={thumbUrl(item.sighting, apiBase)} alt="" />
                <span className="feed-text">
                  <span>{activityText(item)}</span>
                  <span className="muted small">
                    {item.sighting.place || item.sighting.description || item.sighting.objectName}
                  </span>
                </span>
                <time className="muted small">{formatRelative(new Date(item.receivedAt), now)}</time>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
