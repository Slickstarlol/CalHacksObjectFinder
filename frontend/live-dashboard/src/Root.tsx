import type { ComponentProps } from 'react';
import { SpacetimeDBProvider } from 'spacetimedb/react';
import App from './App';
import { AuthScreen } from './components/AuthScreen';
import { useAuth } from './hooks/useAuth';
import { API_BASE } from './config';

type Props = {
  connectionBuilder: ComponentProps<typeof SpacetimeDBProvider>['connectionBuilder'];
  apiBase?: string;
  fetchImpl?: typeof fetch;
};

/**
 * Auth gate. Signed out (or checking a saved session) -> only the sign-in
 * screen; nothing connects to SpacetimeDB. Signed in -> the live dashboard,
 * keyed by user so signing in as someone else starts from a clean slate.
 * A 401 from the API (expired/revoked session) signs out and comes back here.
 */
export default function Root({ connectionBuilder, apiBase = API_BASE, fetchImpl }: Props) {
  const auth = useAuth(apiBase, fetchImpl);
  if (auth.status !== 'signedIn' || !auth.session) {
    return (
      <AuthScreen
        onSignIn={auth.signIn}
        onSignUp={auth.signUp}
        notice={auth.notice}
        checking={auth.status === 'checking'}
      />
    );
  }
  const { user } = auth.session;
  return (
    <SpacetimeDBProvider key={user.id} connectionBuilder={connectionBuilder}>
      <App user={user} authFetch={auth.authFetch} onSignOut={auth.signOut} />
    </SpacetimeDBProvider>
  );
}
