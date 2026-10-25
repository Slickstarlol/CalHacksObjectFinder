import { useEffect } from 'react';

type Options = { minIntervalMs?: number };

/**
 * Shares this browser's location with RecallAI while the dashboard is open:
 * the browser asks for permission once, then each new fix is POSTed to
 * /api/gps (at most every minIntervalMs). The camera attaches it to your
 * sightings and the watcher uses it to notice you walking away from things.
 * Location is only available on localhost or https.
 */
export function useGeolocation(apiBase: string, authFetch: typeof fetch, { minIntervalMs = 10_000 }: Options = {}): void {
  const base = apiBase.replace(/\/+$/, '');

  useEffect(() => {
    const geo = typeof navigator !== 'undefined' ? navigator.geolocation : undefined;
    if (!geo) return;
    let lastSent = 0;
    const id = geo.watchPosition(
      pos => {
        const t = Date.now();
        if (t - lastSent < minIntervalMs) return;
        lastSent = t;
        const { latitude, longitude, accuracy } = pos.coords;
        authFetch(`${base}/api/gps`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ latitude, longitude, accuracy }),
        }).catch(() => {
          lastSent = 0; // server unreachable: try again with the next fix
        });
      },
      () => {
        /* permission denied or no fix: the dashboard works without GPS */
      },
      { enableHighAccuracy: true, maximumAge: 5_000, timeout: 20_000 }
    );
    return () => geo.clearWatch(id);
  }, [base, authFetch, minIntervalMs]);
}
