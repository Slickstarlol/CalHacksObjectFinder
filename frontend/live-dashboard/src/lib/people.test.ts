import { describe, expect, it, vi } from 'vitest';
import { knownFor, renamePerson, RenameError, sortPeople } from './people';
import type { Person } from '../module_bindings/types';

const at = (y: number, m: number, d: number) => new Date(y, m - 1, d, 12);

describe('knownFor (matches people.known_for)', () => {
  it.each([
    ['2024-09-01', at(2026, 10, 24), '2 years and 1 month'],
    ['2025-10-24', at(2026, 10, 24), '1 year'],
    ['2026-08-25', at(2026, 10, 24), '1 month'],
    ['2026-10-01', at(2026, 10, 24), '23 days'],
    ['2026-10-23', at(2026, 10, 24), '1 day'],
    ['2026-10-24', at(2026, 10, 24), 'since today'],
    ['2024-01-31', at(2024, 3, 1), '1 month'],
  ])('%s on %s -> %s', (since, now, want) => {
    expect(knownFor(since, now)).toBe(want);
  });

  it('returns null for empty, invalid or future dates', () => {
    expect(knownFor('', at(2026, 10, 24))).toBeNull();
    expect(knownFor('soon', at(2026, 10, 24))).toBeNull();
    expect(knownFor('2026-02-30', at(2026, 10, 24))).toBeNull();
    expect(knownFor('2026-10-25', at(2026, 10, 24))).toBeNull();
  });
});

describe('sortPeople', () => {
  it('sorts by name case-insensitively, then id', () => {
    const p = (id: bigint, name: string): Person => ({
      id, name, description: '', relationship: '', knownSince: '',
      createdAtMicros: 0n, updatedAtMicros: 0n, owner: '1',
    });
    const out = sortPeople([p(3n, 'kent'), p(2n, 'Chawin'), p(1n, 'Kent')]);
    expect(out.map(x => x.id)).toEqual([2n, 1n, 3n]);
  });
});

describe('renamePerson', () => {
  const person = { id: 7n, description: 'Tall, glasses.', relationship: 'teammate', knownSince: '' };
  const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });

  it('PUTs the new name with the rest of the row unchanged', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json(200, {}));
    await renamePerson(person, '  Chawin ', '/base/', fetchImpl as unknown as typeof fetch);
    expect(fetchImpl).toHaveBeenCalledWith('/base/api/people/7', expect.objectContaining({ method: 'PUT' }));
    expect(JSON.parse(fetchImpl.mock.calls[0][1].body)).toEqual({
      name: 'Chawin',
      description: 'Tall, glasses.',
      relationship: 'teammate',
      known_since: null,
    });
  });

  it('turns failures into readable errors', async () => {
    const run = (res: Response | Error) =>
      renamePerson(person, 'x', '', (() => (res instanceof Error ? Promise.reject(res) : Promise.resolve(res))) as unknown as typeof fetch);
    await expect(run(json(404, { detail: 'No such person' }))).rejects.toThrow('That person no longer exists.');
    await expect(run(json(422, { detail: 'Name is required' }))).rejects.toThrow('Couldn’t save the name: Name is required');
    await expect(run(new TypeError('offline'))).rejects.toBeInstanceOf(RenameError);
  });
});
