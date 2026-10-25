/** Client for FastAPI POST /api/ask (Grok agent). */

export type ChatTurn = { role: 'user' | 'assistant'; content: string };

export type AskRequest = {
  question: string;
  history: ChatTurn[];
  timezone: string;
};

/** A cited sighting row as returned by the API (SQLite row dict, snake_case). */
export type ApiSighting = {
  id: number;
  belonging_name?: string | null;
  object_name?: string | null;
  crop_path?: string | null;
  image_path?: string | null;
  timestamp?: string | null;
  description?: string | null;
  [k: string]: unknown;
};

export type AskStep = { tool: string; input: unknown };

export type AskResponse = {
  answer: string;
  sightings: ApiSighting[];
  steps: AskStep[];
  lookups?: unknown[];
};

export class AskError extends Error {
  constructor(
    message: string,
    readonly status?: number
  ) {
    super(message);
    this.name = 'AskError';
  }
}

/** Keep only the last `maxTurns` messages of history (the server also trims to 10). */
export function trimHistory(history: readonly ChatTurn[], maxTurns = 8): ChatTurn[] {
  return history.slice(-maxTurns);
}

function detailOf(body: unknown): string | undefined {
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === 'string') return d;
    if (d != null) return JSON.stringify(d);
  }
  return undefined;
}

/** Normalise whatever the server sent into a well-formed AskResponse. */
export function normalizeResponse(body: unknown): AskResponse {
  const b = (body ?? {}) as Partial<AskResponse>;
  return {
    answer: typeof b.answer === 'string' ? b.answer : '',
    sightings: Array.isArray(b.sightings) ? b.sightings : [],
    steps: Array.isArray(b.steps) ? b.steps : [],
    ...(Array.isArray(b.lookups) ? { lookups: b.lookups } : {}),
  };
}

export async function askRecall(
  req: AskRequest,
  apiBase = '',
  fetchImpl: typeof fetch = fetch,
  signal?: AbortSignal
): Promise<AskResponse> {
  let res: Response;
  try {
    res = await fetchImpl(`${apiBase.replace(/\/+$/, '')}/api/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
      signal,
    });
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') throw e;
    throw new AskError(
      "Can't reach the RecallAI server on port 8000. Start it with: " +
        'python -m uvicorn recallai.api.server:app --host 0.0.0.0 --port 8000'
    );
  }

  let body: unknown = undefined;
  try {
    body = await res.json();
  } catch {
    /* non-JSON body */
  }

  if (!res.ok) {
    const detail = detailOf(body);
    if (res.status === 502) {
      throw new AskError(
        `Grok couldn't answer right now${detail ? `: ${detail.replace(/^Grok request failed:\s*/, '')}` : '.'}`,
        502
      );
    }
    throw new AskError(
      `The server returned ${res.status}${detail ? `: ${detail}` : ''}.`,
      res.status
    );
  }
  if (body === undefined) throw new AskError('The server sent an unreadable response.', res.status);
  return normalizeResponse(body);
}

/** Short human summary of a tool step for the "How Grok found this" list. */
export function describeStep(step: AskStep): string {
  const input = step.input;
  if (input && typeof input === 'object') {
    const parts = Object.entries(input as Record<string, unknown>)
      .filter(([, v]) => v !== null && v !== undefined && v !== '')
      .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`);
    return parts.length ? `${step.tool} (${parts.join(', ')})` : step.tool;
  }
  return input === undefined || input === null || input === ''
    ? step.tool
    : `${step.tool} (${String(input)})`;
}
