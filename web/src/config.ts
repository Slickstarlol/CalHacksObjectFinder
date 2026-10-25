import { defaultSpacetimeHost } from './lib/host';

export const SPACETIMEDB_HOST: string =
  import.meta.env.VITE_SPACETIMEDB_HOST ||
  defaultSpacetimeHost(typeof window !== 'undefined' ? window.location : undefined);
export const SPACETIMEDB_DB_NAME: string =
  import.meta.env.VITE_SPACETIMEDB_DB_NAME || 'recallai';
/** "" = same origin (Vite dev proxy -> :8000, or FastAPI serving /app in prod). */
export const API_BASE: string = (import.meta.env.VITE_API_BASE || '').replace(/\/+$/, '');
