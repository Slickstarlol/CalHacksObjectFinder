import { useEffect, useState } from 'react';
import { EMPTY_SNAPSHOT, parseLive, type CameraState, type LiveSnapshot } from '../lib/live';
import { resolveUrl } from '../lib/url';

type Options = { pollMs?: number; frameMs?: number };

/**
 * Mirrors the Python camera: polls GET /api/live (status + boxes, bearer auth)
 * and, while the camera is online, reloads /api/live/frame.jpg (an <img> URL
 * with ?token=). Each frame is preloaded so the picture never flashes blank.
 */
export function useLiveCamera(
  apiBase: string,
  authFetch: typeof fetch,
  { pollMs = 1000, frameMs = 150 }: Options = {}
): CameraState {
  const [snap, setSnap] = useState<LiveSnapshot>(EMPTY_SNAPSHOT);
  const [reach, setReach] = useState<'unknown' | 'ok' | 'down'>('unknown');
  const [frameUrl, setFrameUrl] = useState('');
  const base = apiBase.replace(/\/+$/, '');

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const res = await authFetch(`${base}/api/live`);
        if (!res?.ok) throw new Error(`status ${res?.status}`);
        const body = parseLive(await res.json());
        if (!stop) {
          setSnap(body);
          setReach('ok');
        }
      } catch {
        if (!stop) {
          setSnap(EMPTY_SNAPSHOT);
          setReach('down');
        }
      }
      if (!stop) timer = setTimeout(tick, pollMs);
    };
    void tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [base, authFetch, pollMs]);

  useEffect(() => {
    if (!snap.online || typeof Image === 'undefined') {
      setFrameUrl('');
      return;
    }
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => {
      const url = resolveUrl(`/api/live/frame.jpg?t=${Date.now()}`, base);
      const img = new Image();
      img.onload = () => {
        if (stop) return;
        setFrameUrl(url);
        timer = setTimeout(tick, frameMs);
      };
      img.onerror = () => {
        if (!stop) timer = setTimeout(tick, frameMs * 5);
      };
      img.src = url;
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [snap.online, base, frameMs]);

  const status = reach === 'unknown' ? 'connecting' : reach === 'down' ? 'unreachable' : snap.online ? 'online' : 'offline';
  return { ...snap, status, frameUrl: snap.online ? frameUrl : '' };
}
