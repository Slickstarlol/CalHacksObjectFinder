import { mediaUrl } from './auth';

/**
 * Resolve an image/API path stored in the DB (e.g. "/snapshots/x.jpg" or the
 * agent's relative "snapshots/x.jpg") against the FastAPI base.
 * apiBase "" keeps it same-origin (Vite proxy in dev, FastAPI in prod).
 * When signed in, snapshot URLs get ?token= (an <img> can't send headers;
 * FastAPI only serves a snapshot to the account that owns it).
 */
export function resolveUrl(path: string, apiBase = ''): string {
  if (!path) return '';
  if (/^(https?:|data:|blob:)/i.test(path)) return path;
  const p = path.startsWith('/') ? path : `/${path}`;
  return mediaUrl(`${apiBase.replace(/\/+$/, '')}${p}`);
}
