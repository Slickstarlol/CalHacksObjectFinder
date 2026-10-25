import { describe, expect, it, vi } from 'vitest';
import { DeleteError, STALE_SERVER, deleteBelongings, renameBelonging } from './belongings';

const res = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const noItem = () => res(404, { detail: 'No registered belonging "x"' });
const noRoute = () => res(404, { detail: 'Not Found' });

describe('renameBelonging', () => {
  it('sends the trimmed name and returns what the server saved', async () => {
    const f = vi.fn().mockResolvedValue(res(200, { id: 'bag1', name: 'Blue bag' }));
    await expect(renameBelonging('bag1', '  Blue  bag ', '', f)).resolves.toBe('Blue bag');
    expect(f.mock.calls[0][1]).toMatchObject({ method: 'PUT', body: JSON.stringify({ name: 'Blue  bag' }) });
  });
  it('tells a missing item apart from a server too old to rename', async () => {
    await expect(renameBelonging('x', 'n', '', vi.fn().mockResolvedValue(noItem()))).rejects.toThrow('no longer exists');
    await expect(renameBelonging('x', 'n', '', vi.fn().mockResolvedValue(noRoute()))).rejects.toThrow(STALE_SERVER);
    await expect(renameBelonging('x', 'n', '', vi.fn().mockResolvedValue(res(405, { detail: 'Method Not Allowed' })))).rejects.toThrow(
      STALE_SERVER
    );
  });
});

describe('deleteBelongings', () => {
  it('deletes each id and treats already-gone as deleted', async () => {
    const f = vi.fn().mockResolvedValueOnce(res(200)).mockResolvedValueOnce(noItem());
    await expect(deleteBelongings(['bag1', 'my mouse'], 'http://api:8000/', f)).resolves.toEqual(['bag1', 'my mouse']);
    expect(f.mock.calls.map(c => [c[0], c[1].method])).toEqual([
      ['http://api:8000/api/belongings/bag1', 'DELETE'],
      ['http://api:8000/api/belongings/my%20mouse', 'DELETE'],
    ]);
  });

  it('reports failures but keeps what was deleted', async () => {
    const f = vi.fn().mockResolvedValueOnce(res(200)).mockResolvedValueOnce(res(409));
    const err = await deleteBelongings(['a', 'b'], '', f).catch(e => e);
    expect(err).toBeInstanceOf(DeleteError);
    expect(err.deleted).toEqual(['a']);
    expect(err.message).toMatch(/one belonging/);
  });

  it('never counts an old server (no delete route) as deleted', async () => {
    const f = vi.fn().mockResolvedValueOnce(res(200)).mockResolvedValueOnce(noRoute());
    const err = await deleteBelongings(['a', 'b', 'c'], '', f).catch(e => e);
    expect(err.message).toBe(STALE_SERVER);
    expect(err.deleted).toEqual(['a']);
    expect(f).toHaveBeenCalledTimes(2);
  });

  it('says when the server is unreachable', async () => {
    const f = vi.fn().mockRejectedValue(new TypeError('fetch failed'));
    await expect(deleteBelongings(['a'], '', f)).rejects.toThrow(/Can’t reach/);
  });
});
