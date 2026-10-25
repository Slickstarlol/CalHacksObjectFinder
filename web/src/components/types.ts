import type { AskResponse } from '../lib/ask';

export type ConnectionStatus = 'connecting' | 'live' | 'disconnected';

export type AskEntry = {
  id: number;
  question: string;
  status: 'pending' | 'done' | 'error';
  response?: AskResponse;
  error?: string;
};

export type OpenImage = (src: string, caption?: string) => void;
