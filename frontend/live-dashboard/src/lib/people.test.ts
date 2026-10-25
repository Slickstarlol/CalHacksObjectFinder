import { describe, expect, it, vi } from 'vitest';
import { describePerson, knownFor, labelPerson, renamePerson, RenameError, sortPeople } from './people';
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

describe('labelPerson', () => {
  const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
  const bbox: [number, number, number, number] = [10, 20, 60, 200];

  it('POSTs the box with a new name, or with the person it is', async () => {
    const fetchImpl = vi.fn(async (_url: string, _init?: RequestInit) =>
      json(200, { person: { id: 9, name: 'Maya' }, created: true, bbox: [12, 22, 60, 198] })
    );
    const sent = (call: number) => JSON.parse(String(fetchImpl.mock.calls[call][1]?.body));
    const res = await labelPerson({ bbox, name: ' Maya ' }, '/base/', fetchImpl as unknown as typeof fetch);
    expect(fetchImpl).toHaveBeenCalledWith('/base/api/live/people', expect.objectContaining({ method: 'POST' }));
    expect(sent(0)).toEqual({ bbox, name: 'Maya' });
    expect(res).toEqual({ personId: 9, name: 'Maya', created: true, bbox: [12, 22, 60, 198] });

    await labelPerson({ bbox, name: 'Mom', personId: 2 }, '', fetchImpl as unknown as typeof fetch);
    expect(sent(1)).toEqual({ bbox, person_id: 2 });
  });

  it('passes on the server\'s reason when it can\'t', async () => {
    const run = (res: Response | Error) =>
      labelPerson({ bbox, name: 'x' }, '', (() => (res instanceof Error ? Promise.reject(res) : Promise.resolve(res))) as unknown as typeof fetch);
    await expect(run(json(409, { detail: "That person isn't in view any more." }))).rejects.toThrow(
      "That person isn't in view any more."
    );
    await expect(run(json(404, { detail: 'No such person' }))).rejects.toThrow('That person no longer exists.');
    await expect(run(new TypeError('offline'))).rejects.toThrow("Can't reach the RecallAI server");
  });
});

describe('describePerson', () => {
  const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });

  it('POSTs refresh and maps the answer', async () => {
    const fetchImpl = vi.fn(async (_url: string, _init?: RequestInit) =>
      json(200, { summary: 'Red jacket', description: 'Red jacket.', same_person: 'no',
                  photos: ['/api/people/3/photos/reference_1.jpg', 7], saved_to_notes: true, cached: false })
    );
    const res = await describePerson(3n, true, '/base/', fetchImpl as unknown as typeof fetch);
    expect(fetchImpl.mock.calls[0][0]).toBe('/base/api/people/3/describe');
    expect(JSON.parse(String(fetchImpl.mock.calls[0][1]?.body))).toEqual({ refresh: true });
    expect(res).toEqual({
      summary: 'Red jacket', description: 'Red jacket.', samePerson: 'no',
      photos: ['/api/people/3/photos/reference_1.jpg'], savedToNotes: true,
    });
  });

  it('passes on why Grok could not answer', async () => {
    const run = (res: Response | Error) =>
      describePerson(3n, false, '', (() => (res instanceof Error ? Promise.reject(res) : Promise.resolve(res))) as unknown as typeof fetch);
    await expect(run(json(409, { detail: 'No photos of Mom yet.' }))).rejects.toThrow('No photos of Mom yet.');
    await expect(run(json(503, { detail: "Grok isn't set up" }))).rejects.toThrow("Grok isn't set up");
    await expect(run(json(404, {}))).rejects.toThrow('That person no longer exists.');
    await expect(run(new TypeError('offline'))).rejects.toThrow("Can't reach the RecallAI server.");
  });
});
