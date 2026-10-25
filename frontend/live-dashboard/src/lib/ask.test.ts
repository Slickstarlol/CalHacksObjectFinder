import { describe, expect, it, vi } from 'vitest';
import { AskError, askRecall, describeStep, normalizeResponse, trimHistory } from './ask';

const req = { question: 'where is my bag?', history: [], timezone: 'America/Los_Angeles' };
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('askRecall', () => {
  it('POSTs the question to /api/ask and normalises the response', async () => {
    const f = vi.fn().mockResolvedValue(json(200, { answer: 'On the chair [#3]', sightings: [{ id: 3 }], steps: [] }));
    const res = await askRecall(req, 'http://h:8000/', f);
    expect(f).toHaveBeenCalledOnce();
    const [url, init] = f.mock.calls[0];
    expect(url).toBe('http://h:8000/api/ask');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual(req);
    expect(res.answer).toBe('On the chair [#3]');
    expect(res.sightings[0].id).toBe(3);
  });

  it('maps 502 to a friendly Grok error with the detail', async () => {
    const f = vi.fn().mockResolvedValue(json(502, { detail: 'Grok request failed: timeout' }));
    await expect(askRecall(req, '', f)).rejects.toMatchObject({ status: 502, message: "Grok couldn't answer right now: timeout" });
  });

  it('maps network failures to a reachability message', async () => {
    const f = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    const err = await askRecall(req, '', f).catch(e => e);
    expect(err).toBeInstanceOf(AskError);
    expect(err.message).toMatch(/Can't reach/);
  });

  it('reports other statuses and unreadable bodies', async () => {
    await expect(askRecall(req, '', vi.fn().mockResolvedValue(json(422, { detail: [{ msg: 'bad' }] })))).rejects.toThrow(/422/);
    await expect(askRecall(req, '', vi.fn().mockResolvedValue(new Response('oops', { status: 200 })))).rejects.toThrow(/unreadable/);
  });
});

describe('helpers', () => {
  it('normalises missing fields', () => {
    expect(normalizeResponse({ answer: 'hi' })).toEqual({ answer: 'hi', sightings: [], steps: [] });
    expect(normalizeResponse(null).answer).toBe('');
  });
  it('trims history to the last N turns', () => {
    const h = Array.from({ length: 12 }, (_, i) => ({ role: 'user' as const, content: String(i) }));
    expect(trimHistory(h, 4).map(t => t.content)).toEqual(['8', '9', '10', '11']);
  });
  it('describes tool steps', () => {
    expect(describeStep({ tool: 'recent_sightings', input: { belonging_id: 'b1', limit: 5, x: null } })).toBe(
      'recent_sightings (belonging_id: b1, limit: 5)'
    );
    expect(describeStep({ tool: 'list', input: {} })).toBe('list');
    expect(describeStep({ tool: 'q', input: 'text' })).toBe('q (text)');
  });
});
