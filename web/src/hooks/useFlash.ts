import { useEffect, useRef, useState } from 'react';

/**
 * Returns true for `ms` after `key` changes (not on first render, and only
 * while `enabled` - e.g. after the initial subscription has loaded).
 */
export function useFlash(key: string, enabled: boolean, ms = 2200): boolean {
  const prev = useRef(key);
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (prev.current === key) return;
    prev.current = key;
    if (!enabled) return;
    setFlash(true);
    const t = setTimeout(() => setFlash(false), ms);
    return () => clearTimeout(t);
  }, [key, enabled, ms]);
  return flash;
}
