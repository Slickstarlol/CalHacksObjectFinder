import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AuthError,
  fetchMe,
  loadSession,
  login as apiLogin,
  logout as apiLogout,
  makeAuthFetch,
  register as apiRegister,
  saveSession,
  setMediaToken,
  type RegisterForm,
  type Session,
} from '../lib/auth';

export type AuthStatus = 'checking' | 'signedOut' | 'signedIn';

export type AuthState = {
  status: AuthStatus;
  session: Session | null;
  /** Why the user is looking at the sign-in screen (e.g. session expired). */
  notice: string | null;
  signIn: (username: string, password: string) => Promise<void>;
  signUp: (form: RegisterForm) => Promise<void>;
  signOut: () => void;
  /** fetch() with the bearer token; a 401 signs out. */
  authFetch: typeof fetch;
};

const EXPIRED = 'Your session has ended. Please sign in again.';

/** Session state machine: restore -> verify with /api/auth/me -> signed in/out. */
export function useAuth(apiBase: string, fetchImpl: typeof fetch = (...a) => fetch(...a)): AuthState {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [status, setStatus] = useState<AuthStatus>(() => (session ? 'checking' : 'signedOut'));
  const [notice, setNotice] = useState<string | null>(null);
  const fetchRef = useRef(fetchImpl);
  fetchRef.current = fetchImpl;

  const apply = useCallback((s: Session | null, why: string | null = null) => {
    saveSession(s);
    setMediaToken(s?.token ?? null);
    setSession(s);
    setStatus(s ? 'signedIn' : 'signedOut');
    setNotice(why);
  }, []);

  // Restore: check the stored token is still valid.
  const restored = useRef(false);
  useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    const saved = loadSession();
    if (!saved) return;
    setMediaToken(saved.token);
    fetchMe(saved.token, apiBase, fetchRef.current)
      .then(user => apply({ ...saved, user }))
      .catch(err => {
        if (err instanceof AuthError && err.status === 401) apply(null, EXPIRED);
        else apply(saved); // server unreachable: keep the session, API calls will retry
      });
  }, [apiBase, apply]);

  const signIn = useCallback(
    async (username: string, password: string) => {
      apply(await apiLogin(username, password, apiBase, fetchRef.current));
    },
    [apiBase, apply]
  );

  const signUp = useCallback(
    async (form: RegisterForm) => {
      apply(await apiRegister(form, apiBase, fetchRef.current));
    },
    [apiBase, apply]
  );

  const signOut = useCallback(() => {
    const token = session?.token;
    apply(null);
    if (token) void apiLogout(token, apiBase, fetchRef.current);
  }, [apiBase, apply, session]);

  const token = session?.token ?? '';
  const authFetch = useMemo(
    () => makeAuthFetch(token, () => apply(null, EXPIRED), (...a) => fetchRef.current(...a)),
    [token, apply]
  );

  return { status, session, notice, signIn, signUp, signOut, authFetch };
}
