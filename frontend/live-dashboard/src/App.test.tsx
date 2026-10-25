/** Container test: the SpacetimeDB React hooks are mocked, no server needed. */
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { belongings, makeAlerts, makePeople, makeSighting, makeSightings, micros } from './demo/fixtures';
import type { Sighting } from './module_bindings/types';

const NOW = Date.now();
const state = {
  isActive: true,
  belongings,
  sightings: makeSightings(NOW) as Sighting[],
  alerts: makeAlerts(NOW).filter(a => !a.dismissed),
  people: makePeople(NOW),
  callbacks: {} as { onInsert?: (r: Sighting) => void; onUpdate?: (o: Sighting, n: Sighting) => void },
  dismiss: vi.fn().mockResolvedValue(undefined),
};

vi.mock('spacetimedb/react', () => {
  let call = 0;
  return {
    useSpacetimeDB: () => ({ isActive: state.isActive, connectionError: undefined }),
    // App calls useTable for belonging, sighting, alert, person in that order on every render.
    useTable: (_q: unknown, cb?: typeof state.callbacks) => {
      const i = call++ % 4;
      if (i === 1 && cb) state.callbacks = cb;
      return [[state.belongings, state.sightings, state.alerts, state.people][i], true];
    },
    useReducer: () => state.dismiss,
  };
});

import App from './App';

const user = { id: 1, username: 'kent', display_name: 'Kent' };
const props = { user, authFetch: vi.fn() as unknown as typeof fetch, onSignOut: vi.fn() };

beforeEach(() => {
  state.dismiss.mockClear();
  props.onSignOut.mockClear();
});

describe('App container', () => {
  it('wires live tables into the dashboard and calls dismissAlert', async () => {
    render(<App {...props} />);
    expect(screen.getByRole('status')).toHaveTextContent('Live');
    expect(screen.getAllByTestId('belonging-card')).toHaveLength(5);
    const dismiss = screen.getAllByRole('button', { name: 'Dismiss' });
    await userEvent.click(dismiss[0]);
    expect(state.dismiss).toHaveBeenCalledWith({ id: 77n });
    // optimistically hidden
    expect(screen.getAllByRole('button', { name: 'Dismiss' })).toHaveLength(1);
    // live person table reaches the People panel
    expect(screen.getAllByTestId('person').map(p => p.textContent)).toEqual([
      expect.stringContaining('Chawin'),
      expect.stringContaining('Mom'),
    ]);
  });

  it('records live sighting inserts in the activity feed', () => {
    render(<App {...props} />);
    const s = makeSighting({
      id: 4242n, belongingId: '365acc452f4e', belongingName: 'My Mouse', seenAtMicros: micros(Date.now()),
    });
    act(() => state.callbacks.onInsert?.(s));
    expect(screen.getByText('My Mouse spotted')).toBeInTheDocument();
    // rejected sightings are not logged
    act(() => state.callbacks.onInsert?.({ ...s, id: 4243n, belongingName: 'Ghost', verified: 0 }));
    expect(screen.queryByText('Ghost spotted')).not.toBeInTheDocument();
  });
});

describe('App container: accounts', () => {
  it("shows only the signed-in user's rows, even if others reach the client", () => {
    const now = Date.now();
    const saved = { ...state };
    state.belongings = [
      ...belongings,
      { id: 'zz', name: 'Someone Else’s Bag', category: 'backpack', photos: 5, tracking: true, owner: '2' },
    ];
    state.alerts = [
      ...makeAlerts(now).filter(a => !a.dismissed),
      { ...makeAlerts(now)[0], id: 99n, message: 'Not your alert', owner: '2' },
    ];
    state.people = [...makePeople(now), { ...makePeople(now)[0], id: 9n, name: 'Stranger', owner: '' }];
    try {
      render(<App {...props} />);
      expect(screen.getAllByTestId('belonging-card')).toHaveLength(5);
      expect(screen.queryByText('Someone Else’s Bag')).not.toBeInTheDocument();
      expect(screen.queryByText('Not your alert')).not.toBeInTheDocument();
      expect(screen.queryByText('Stranger')).not.toBeInTheDocument();
    } finally {
      Object.assign(state, saved);
    }
  });

  it('ignores live inserts for other owners', () => {
    render(<App {...props} />);
    const s = makeSighting({
      id: 5151n, belongingId: 'zz', belongingName: 'Their Bag', seenAtMicros: micros(Date.now()), owner: '2',
    });
    act(() => state.callbacks.onInsert?.(s));
    expect(screen.queryByText('Their Bag spotted')).not.toBeInTheDocument();
  });

  it('shows who is signed in and signs out', async () => {
    render(<App {...props} />);
    expect(screen.getByText('Kent')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(props.onSignOut).toHaveBeenCalledTimes(1);
  });

  it('asks Grok with the authenticated fetch', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ answer: 'On the desk', sightings: [], steps: [] }), { status: 200 })
    );
    render(<App {...props} authFetch={authFetch as unknown as typeof fetch} />);
    await userEvent.type(screen.getByRole('textbox', { name: 'Question for RecallAI' }), 'where is my bag?{Enter}');
    expect(authFetch).toHaveBeenCalledWith('/api/ask', expect.objectContaining({ method: 'POST' }));
  });
});
