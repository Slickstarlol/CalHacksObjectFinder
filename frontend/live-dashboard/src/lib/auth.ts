/**
 * RecallAI accounts on the client: talks to FastAPI /api/auth/*, keeps the
 * session in localStorage, and adds `Authorization: Bearer <token>` to API
 * calls. <img> URLs for snapshots can't carry headers, so resolveUrl() adds
 * `?token=` to /snapshots and /api/live media (see mediaUrl).
 *
 * The storage key is shared with the plain HTML pages served by FastAPI
 * (gps.html, search.html), so one sign-in covers http://<host>:8000/*.
 */

export type AuthUser = {
  id: number;
  username: string;
  display_name: string;
  created_at?: string;
};

export type Session = {
  token: string;
  user: AuthUser;
  expires_at?: string;
};

export const SESSION_KEY = 'recallai.auth';
export const USERNAME_PATTERN = /^[a-z0-9_.-]{3,32}$/;
export const MIN_PASSWORD = 8;

export class AuthError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly field?: string
  ) {
    super(message);
    this.name = 'AuthError';
  }
}

// ---------------------------------------------------------------- storage
type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem' | 'removeItem'>;

function storage(): Storage | undefined {
  try {
    return typeof localStorage === 'undefined' ? undefined : localStorage;
  } catch {
    return undefined;
  }
}

export function loadSession(store: Storage | undefined = storage()): Session | null {
  try {
    const raw = store?.getItem(SESSION_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Partial<Session>;
    if (typeof s?.token !== 'string' || !s.user || typeof s.user.id !== 'number') return null;
    return s as Session;
  } catch {
    return null;
  }
}

export function saveSession(session: Session | null, store: Storage | undefined = storage()): void {
  try {
    if (session) store?.setItem(SESSION_KEY, JSON.stringify(session));
    else store?.removeItem(SESSION_KEY);
  } catch {
    /* private mode: the session lives for this tab only */
  }
}

// Token used for <img> URLs. Set by the auth hook when signing in/out.
let mediaToken: string | null = null;
export function setMediaToken(token: string | null): void {
  mediaToken = token;
}
export function getMediaToken(): string | null {
  return mediaToken;
}

/** Add ?token= to same-server media paths (snapshots, live camera frames). */
export function mediaUrl(url: string, token: string | null = mediaToken): string {
  if (!url || !token) return url;
  let path = url;
  try {
    path = new URL(url, 'http://x').pathname;
  } catch {
    /* relative junk: leave it */
  }
  if (!path.startsWith('/snapshots/') && !path.startsWith('/api/live/')) return url;
  return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
}

/** The `owner` value SpacetimeDB rows carry for this user ("" = ownerless). */
export function ownerKey(user: Pick<AuthUser, 'id'> | null | undefined): string {
  return user ? String(user.id) : '';
}

/** Keep only rows that belong to `owner` (client-side guard; see docs/AUTH.md). */
export function ownedBy<T extends { owner: string }>(rows: readonly T[], owner: string): T[] {
  if (!owner) return [];
  return rows.filter(r => r.owner === owner);
}

// ---------------------------------------------------------------- validation
export type RegisterForm = {
  username: string;
  displayName: string;
  password: string;
  confirm: string;
};

export type FormErrors = Partial<Record<'username' | 'displayName' | 'password' | 'confirm' | 'form', string>>;

export function normalizeUsername(username: string): string {
  return username.trim().toLowerCase();
}

export function validateRegistration(f: RegisterForm): FormErrors {
  const errors: FormErrors = {};
  const u = normalizeUsername(f.username);
  if (u.length < 3 || u.length > 32) errors.username = 'Username must be 3–32 characters.';
  else if (!USERNAME_PATTERN.test(u))
    errors.username = 'Use only lowercase letters, numbers, dots, dashes and underscores.';
  if (f.displayName.trim().length > 64) errors.displayName = 'Display name must be at most 64 characters.';
  if (f.password.length < MIN_PASSWORD) errors.password = `Password must be at least ${MIN_PASSWORD} characters.`;
  else if (f.password.length > 256) errors.password = 'Password must be at most 256 characters.';
  if (!errors.password && f.confirm !== f.password) errors.confirm = 'Passwords do not match.';
  return errors;
}

export function validateLogin(username: string, password: string): FormErrors {
  const errors: FormErrors = {};
  if (!username.trim()) errors.username = 'Enter your username.';
  if (!password) errors.password = 'Enter your password.';
  return errors;
}

// ---------------------------------------------------------------- HTTP
function base(apiBase: string): string {
  return apiBase.replace(/\/+$/, '');
}

function detailOf(body: unknown): { message?: string; field?: string } {
  if (!body || typeof body !== 'object' || !('detail' in body)) return {};
  const d = (body as { detail: unknown }).detail;
  if (typeof d === 'string') return { message: d };
  if (Array.isArray(d)) return { message: d.map(x => (x && typeof x === 'object' && 'msg' in x ? String(x.msg) : String(x))).join(' ') };
  if (d && typeof d === 'object') {
    const o = d as { message?: unknown; field?: unknown };
    return {
      message: typeof o.message === 'string' ? o.message : undefined,
      field: typeof o.field === 'string' ? o.field : undefined,
    };
  }
  return {};
}

async function call(
  url: string,
  init: RequestInit,
  fetchImpl: typeof fetch
): Promise<unknown> {
  let res: Response;
  try {
    res = await fetchImpl(url, init);
  } catch {
    throw new AuthError(
      "Can't reach the RecallAI server on port 8000. Start it with: " +
        'python -m uvicorn recallai.api.server:app --host 0.0.0.0 --port 8000'
    );
  }
  let body: unknown = undefined;
  try {
    body = await res.json();
  } catch {
    /* empty or non-JSON */
  }
  if (!res.ok) {
    const { message, field } = detailOf(body);
    throw new AuthError(message || `The server returned ${res.status}.`, res.status, field);
  }
  return body;
}

function toSession(body: unknown): Session {
  const b = body as Partial<Session> | undefined;
  if (!b || typeof b.token !== 'string' || !b.user) throw new AuthError('The server sent an unreadable response.');
  return { token: b.token, user: b.user, expires_at: b.expires_at };
}

export async function login(
  username: string,
  password: string,
  apiBase = '',
  fetchImpl: typeof fetch = fetch
): Promise<Session> {
  const body = await call(
    `${base(apiBase)}/api/auth/login`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: normalizeUsername(username), password }),
    },
    fetchImpl
  );
  return toSession(body);
}

export async function register(
  form: RegisterForm,
  apiBase = '',
  fetchImpl: typeof fetch = fetch
): Promise<Session> {
  const body = await call(
    `${base(apiBase)}/api/auth/register`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: normalizeUsername(form.username),
        display_name: form.displayName.trim(),
        password: form.password,
      }),
    },
    fetchImpl
  );
  return toSession(body);
}

export async function fetchMe(token: string, apiBase = '', fetchImpl: typeof fetch = fetch): Promise<AuthUser> {
  return (await call(
    `${base(apiBase)}/api/auth/me`,
    { headers: { Authorization: `Bearer ${token}` } },
    fetchImpl
  )) as AuthUser;
}

export async function logout(token: string, apiBase = '', fetchImpl: typeof fetch = fetch): Promise<void> {
  try {
    await fetchImpl(`${base(apiBase)}/api/auth/logout`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    /* signing out locally is what matters */
  }
}

/**
 * fetch() that adds the bearer token and reports a 401 (expired/revoked
 * session) through onUnauthorized, so the app can return to the sign-in screen.
 */
export function makeAuthFetch(
  token: string,
  onUnauthorized: () => void,
  fetchImpl: typeof fetch = (...a) => fetch(...a)
): typeof fetch {
  return async (input, init = {}) => {
    const headers = new Headers(init.headers);
    headers.set('Authorization', `Bearer ${token}`);
    const res = await fetchImpl(input, { ...init, headers });
    if (res.status === 401) onUnauthorized();
    return res;
  };
}
