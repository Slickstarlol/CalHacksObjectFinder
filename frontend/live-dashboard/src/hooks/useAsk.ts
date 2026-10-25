import { useCallback, useRef, useState } from 'react';
import { AskError, askRecall, trimHistory, type ChatTurn } from '../lib/ask';
import { browserTimeZone } from '../lib/time';
import type { AskEntry } from '../components/types';

let nextId = 0;

/** State machine for the "Ask RecallAI" box: entries, pending flag, history. */
export function useAsk(apiBase: string, fetchImpl: typeof fetch = (...a) => fetch(...a)) {
  const [entries, setEntries] = useState<AskEntry[]>([]);
  const history = useRef<ChatTurn[]>([]);
  const pending = entries.some(e => e.status === 'pending');

  const ask = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q) return;
      const id = ++nextId;
      setEntries(prev => [...prev, { id, question: q, status: 'pending' as const }].slice(-6));
      try {
        const res = await askRecall(
          { question: q, history: trimHistory(history.current), timezone: browserTimeZone() },
          apiBase,
          fetchImpl
        );
        history.current = trimHistory([
          ...history.current,
          { role: 'user', content: q },
          { role: 'assistant', content: res.answer },
        ]);
        setEntries(prev => prev.map(e => (e.id === id ? { ...e, status: 'done', response: res } : e)));
      } catch (err) {
        const message = err instanceof AskError ? err.message : 'Something went wrong asking Khris.';
        setEntries(prev => prev.map(e => (e.id === id ? { ...e, status: 'error', error: message } : e)));
      }
    },
    [apiBase, fetchImpl]
  );

  const clear = useCallback(() => {
    history.current = [];
    setEntries([]);
  }, []);

  return { entries, pending, ask, clear };
}
