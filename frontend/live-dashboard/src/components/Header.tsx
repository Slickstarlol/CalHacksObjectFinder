import { Logo } from './Icons';
import { ThemeToggle } from './ThemeToggle';
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
  /** "4m ago": age of the newest sighting, shown beside the Live pill. */
  updated?: string;
  account?: { name: string; username: string; onSignOut: () => void };
};

export function Header({ status, belongings, sightings, alerts, updated, account }: Props) {
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <div className="brand">
          <Logo />
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
        <div className={`status status-${status}`} role="status" title="SpacetimeDB connection">
          <span className="status-dot" aria-hidden="true" />
          <span className="status-text">{LABEL[status]}</span>
          {status === 'live' && updated && (
            <span className="status-age" title="Newest sighting">
              · {updated}
            </span>
          )}
        </div>
        <ThemeToggle />
        {account && (
          <div className="account">
            <span className="account-name" title={`Signed in as ${account.username}`}>
              <span className="visually-hidden">Signed in as </span>
              {account.name}
            </span>
            <button type="button" className="btn btn-on-dark" onClick={account.onSignOut}>
              Sign out
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
