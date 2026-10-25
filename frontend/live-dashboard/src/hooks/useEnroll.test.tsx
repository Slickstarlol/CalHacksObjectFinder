import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useEnroll } from './useEnroll';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const idle = { active: false, phase: 'idle', message: '', name: '', saved: 0, needed: 3 };

describe('useEnroll', () => {
  it('ignores a session that finished before the page opened', async () => {
    const f = vi.fn().mockResolvedValue(json({ ...idle, phase: 'done', name: 'Old cup', saved: 3 }));
    const { result } = renderHook(() => useEnroll('', f, { pollMs: 10 }));
    await waitFor(() => expect(f).toHaveBeenCalled());
    expect(result.current.state).toBeNull();
  });

  it('starts, follows progress, and shows the finished item', async () => {
    const replies = [
      json(idle), // first look on mount
      json({ ...idle, active: true, phase: 'hold', message: 'Show one object and hold it still.' }), // start
      json({ ...idle, active: true, phase: 'turn', name: 'cup', saved: 2, message: 'Turn it. 2 of 3 saved.' }),
      json({ ...idle, phase: 'done', name: 'cup', saved: 3, message: 'cup is in the tracker.' }),
    ];
    const f = vi.fn(() => Promise.resolve(replies.length > 1 ? replies.shift()! : replies[0].clone()));
    const { result } = renderHook(() => useEnroll('http://api/', f, { pollMs: 10 }));
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));
    await act(() => result.current.start());
    expect(f).toHaveBeenLastCalledWith('http://api/api/enroll/start', { method: 'POST' });
    expect(result.current.state).toMatchObject({ active: true, phase: 'hold' });
    await waitFor(() => expect(result.current.state).toMatchObject({ phase: 'done', name: 'cup', saved: 3 }));
  });

  it('reports why the camera could not start', async () => {
    const f = vi
      .fn()
      .mockResolvedValueOnce(json(idle))
      .mockResolvedValueOnce(json({ detail: "The camera is adding another account's item." }, 409));
    const { result } = renderHook(() => useEnroll('', f));
    await act(() => result.current.start());
    expect(result.current.error).toMatch(/another account/);
    expect(result.current.state).toBeNull();
  });
});
