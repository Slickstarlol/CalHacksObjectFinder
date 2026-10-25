import { useEffect, useState } from 'react';

/** Current time, re-rendered every `intervalMs` so relative times tick. */
export function useNow(intervalMs = 30_000, fixed?: Date): Date {
  const [now, setNow] = useState(() => fixed ?? new Date());
  useEffect(() => {
    if (fixed) return;
    const t = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs, fixed]);
  return fixed ?? now;
}
