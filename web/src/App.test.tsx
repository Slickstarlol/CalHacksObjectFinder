/** Container test: the SpacetimeDB React hooks are mocked, no server needed. */
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { belongings, makeAlerts, makeSighting, makeSightings, micros } from './demo/fixtures';
import type { Sighting } from './module_bindings/types';

const NOW = Date.now();
const state = {
  isActive: true,
  belongings,
  sightings: makeSightings(NOW) as Sighting[],
  alerts: makeAlerts(NOW).filter(a => !a.dismissed),
  callbacks: {} as { onInsert?: (r: Sighting) => void; onUpdate?: (o: Sighting, n: Sighting) => void },
  dismiss: vi.fn().mockResolvedValue(undefined),
};

vi.mock('spacetimedb/react', () => {
  let call = 0;
  return {
    useSpacetimeDB: () => ({ isActive: state.isActive, connectionError: undefined }),
    // App calls useTable for belonging, sighting, alert in that order on every render.
    useTable: (_q: unknown, cb?: typeof state.callbacks) => {
      const i = call++ % 3;
      if (i === 1 && cb) state.callbacks = cb;
      return [[state.belongings, state.sightings, state.alerts][i], true];
    },
    useReducer: () => state.dismiss,
  };
});

import App from './App';

beforeEach(() => {
  state.dismiss.mockClear();
});

describe('App container', () => {
  it('wires live tables into the dashboard and calls dismissAlert', async () => {
    render(<App />);
    expect(screen.getByRole('status')).toHaveTextContent('Live');
    expect(screen.getAllByTestId('belonging-card')).toHaveLength(5);
    const dismiss = screen.getAllByRole('button', { name: 'Dismiss' });
    await userEvent.click(dismiss[0]);
    expect(state.dismiss).toHaveBeenCalledWith({ id: 77n });
    // optimistically hidden
    expect(screen.getAllByRole('button', { name: 'Dismiss' })).toHaveLength(1);
  });

  it('records live sighting inserts in the activity feed', () => {
    render(<App />);
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
