/**
 * SpacetimeDB WebSocket URL when VITE_SPACETIMEDB_HOST isn't set: the same
 * machine that served this page, port 3000. So opening the dashboard from a
 * phone at http://<laptop-ip>:8000/app connects to the laptop's SpacetimeDB,
 * not the phone's own "localhost".
 */
export function defaultSpacetimeHost(
  loc: Pick<Location, 'hostname' | 'protocol'> | undefined,
  port = 3000
): string {
  const host = loc?.hostname || 'localhost';
  const scheme = loc?.protocol === 'https:' ? 'wss' : 'ws';
  const h = host.includes(':') && !host.startsWith('[') ? `[${host}]` : host; // IPv6
  return `${scheme}://${h}:${port}`;
}
