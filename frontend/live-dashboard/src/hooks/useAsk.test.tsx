import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useAsk } from './useAsk';

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

describe('useAsk', () => {
  it('sends history from previous turns and records answers', async () => {
    const f = vi.fn()
      .mockResolvedValueOnce(ok({ answer: 'On the desk [#1]', sightings: [], steps: [] }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'boom' }), { status: 502 }));
    const { result } = renderHook(() => useAsk('', f));
    await act(() => result.current.ask('where is my laptop?'));
    expect(result.current.entries[0]).toMatchObject({ status: 'done', response: { answer: 'On the desk [#1]' } });
    await act(() => result.current.ask('and before that?'));
    const body = JSON.parse(f.mock.calls[1][1].body);
    expect(body.history).toEqual([
      { role: 'user', content: 'where is my laptop?' },
      { role: 'assistant', content: 'On the desk [#1]' },
    ]);
    expect(typeof body.timezone).toBe('string');
    await waitFor(() => expect(result.current.entries[1].status).toBe('error'));
    expect(result.current.entries[1].error).toMatch(/Khris couldn't answer/);
    act(() => result.current.clear());
    expect(result.current.entries).toEqual([]);
  });

  it('ignores blank questions', async () => {
    const f = vi.fn();
    const { result } = renderHook(() => useAsk('', f));
    await act(() => result.current.ask('   '));
    expect(f).not.toHaveBeenCalled();
  });
});
