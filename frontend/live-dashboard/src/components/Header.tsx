import type { ConnectionStatus } from './types';

const LABEL: Record<ConnectionStatus, string> = {
  connecting: 'Connecting',
  live: 'Live',
  disconnected: 'Disconnected',
};

type Props = {
  status: ConnectionStatus;
  belongings: number;
  sightings: number;
  alerts: number;
};

export function Header({ status, belongings, sightings, alerts }: Props) {
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <h1>RecallAI</h1>
        </div>
        <dl className="counts" aria-label="Counts">
          <div>
            <dt>Belongings</dt>
            <dd>{belongings}</dd>
          </div>
          <div>
            <dt>Sightings</dt>
            <dd>{sightings}</dd>
          </div>
          <div className={alerts > 0 ? 'has-alerts' : ''}>
            <dt>Alerts</dt>
            <dd>{alerts}</dd>
          </div>
        </dl>
        <a className="btn btn-primary ask-jump" href="#ask">
          Ask
        </a>
        <div className={`status status-${status}`} role="status" title="SpacetimeDB connection">
          <span className="status-dot" aria-hidden="true" />
          <span className="status-text">{LABEL[status]}</span>
        </div>
      </div>
    </header>
  );
}
