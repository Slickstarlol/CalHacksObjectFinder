import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import type { Identity } from 'spacetimedb';
import { DbConnection, type ErrorContext } from './module_bindings';
import { SPACETIMEDB_DB_NAME, SPACETIMEDB_HOST } from './config';
import Root from './Root';
import './styles.css';

const TOKEN_KEY = `${SPACETIMEDB_HOST}/${SPACETIMEDB_DB_NAME}/auth_token`;
const RETRIED_KEY = `${TOKEN_KEY}/reset_once`;

function readToken(): string | undefined {
  try {
    return localStorage.getItem(TOKEN_KEY) || undefined;
  } catch {
    return undefined;
  }
}

const connectionBuilder = DbConnection.builder()
  .withUri(SPACETIMEDB_HOST)
  .withDatabaseName(SPACETIMEDB_DB_NAME)
  .withToken(readToken())
  .onConnect((_conn: DbConnection, identity: Identity, token: string) => {
    try {
      localStorage.setItem(TOKEN_KEY, token);
      sessionStorage.removeItem(RETRIED_KEY);
    } catch {
      /* private mode */
    }
    console.info('[RecallAI] connected to SpacetimeDB as', identity.toHexString());
  })
  .onDisconnect(() => console.info('[RecallAI] disconnected from SpacetimeDB'))
  .onConnectError((_ctx: ErrorContext, err: Error) => {
    console.warn('[RecallAI] SpacetimeDB connection error:', err);
    // A saved token stops working if SpacetimeDB's data was wiped (new keys).
    // Forget it and reload once with a fresh anonymous identity.
    try {
      if (localStorage.getItem(TOKEN_KEY) && !sessionStorage.getItem(RETRIED_KEY)) {
        localStorage.removeItem(TOKEN_KEY);
        sessionStorage.setItem(RETRIED_KEY, '1');
        window.location.reload();
      }
    } catch {
      /* storage unavailable: nothing to reset */
    }
  });

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* Sign-in screen until signed in; then the live dashboard (Root.tsx). */}
    <Root connectionBuilder={connectionBuilder} />
  </StrictMode>
);
