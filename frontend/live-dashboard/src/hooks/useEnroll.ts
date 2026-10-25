import { useCallback, useEffect, useRef, useState } from 'react';

/** GET /api/enroll (server.py enroll_view_for): the camera's "Add" session. */
export type EnrollState = {
  active: boolean;
  /** idle | hold | turn | done | busy (another account is adding). */
  phase: string;
  message: string;
  name: string;
  saved: number;
  needed: number;
};

export type Enroll = {
  state: EnrollState | null;
  /** Shown under the button: a failed start/cancel. */
  error: string | null;
  start: () => Promise<void>;
  cancel: () => Promise<void>;
};

const DONE_FOR_MS = 6000;

function parse(body: unknown): EnrollState | null {
  if (!body || typeof body !== 'object') return null;
  const b = body as Record<string, unknown>;
  return {
    active: b.active === true,
    phase: typeof b.phase === 'string' ? b.phase : 'idle',
    message: typeof b.message === 'string' ? b.message : '',
    name: typeof b.name === 'string' ? b.name : '',
    saved: Number(b.saved) || 0,
    needed: Number(b.needed) || 0,
  };
}

async function detail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return typeof body.detail === 'string' ? body.detail : '';
  } catch {
    return '';
  }
}

/**
 * Adding a belonging with the live camera (the "+" button). Start asks the
 * camera to collect still angles of whatever you hold up; while a session is
 * on it polls the server every `pollMs` for progress. A finished session
 * stays visible for a few seconds so you see that it worked.
 */
export function useEnroll(apiBase: string, authFetch: typeof fetch, { pollMs = 1000 } = {}): Enroll {
  const base = apiBase.replace(/\/+$/, '');
  const [state, setState] = useState<EnrollState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const doneAt = useRef<number | null>(null);

  const apply = useCallback((next: EnrollState | null) => {
    if (next?.phase === 'done' && !next.active) {
      doneAt.current ??= Date.now();
      if (Date.now() - doneAt.current > DONE_FOR_MS) next = null;
    } else {
      doneAt.current = null;
    }
    setState(next && (next.active || next.phase === 'done') ? next : null);
  }, []);

  const poll = useCallback(async (initial = false) => {
    try {
      const res = await authFetch(`${base}/api/enroll`);
      if (!res.ok) return;
      const next = parse(await res.json());
      // A session that finished before this page opened isn't news.
      apply(initial && !next?.active ? null : next);
    } catch {
      /* server unreachable: keep the last state */
    }
  }, [authFetch, base, apply]);

  // Pick up a session already running (e.g. after a reload), then poll while one is on.
  useEffect(() => {
    void poll(true);
  }, [poll]);
  const showing = state !== null;
  useEffect(() => {
    if (!showing) return;
    const t = setInterval(() => void poll(), pollMs);
    return () => clearInterval(t);
  }, [showing, poll, pollMs]);

  const send = useCallback(
    async (path: string, failed: string) => {
      setError(null);
      try {
        const res = await authFetch(`${base}${path}`, { method: 'POST' });
        if (!res.ok) {
          setError((await detail(res)) || failed);
          return;
        }
        doneAt.current = null;
        apply(parse(await res.json()));
      } catch {
        setError('Can’t reach the RecallAI server on port 8000.');
      }
    },
    [authFetch, base, apply]
  );

  return {
    state,
    error,
    start: useCallback(() => send('/api/enroll/start', 'Couldn’t start adding an item.'), [send]),
    cancel: useCallback(() => send('/api/enroll/cancel', 'Couldn’t stop adding the item.'), [send]),
  };
}
