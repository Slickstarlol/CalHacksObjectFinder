import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Dashboard, type DashboardProps } from './Dashboard';
import { askEntries, belongings, makeActivity, makeAlerts, makeSighting, makeSightings, micros } from '../demo/fixtures';

const NOW = new Date('2026-10-24T15:00:00Z').getTime();

function setup(overrides: Partial<DashboardProps> = {}) {
  const sightings = makeSightings(NOW);
  const props: DashboardProps = {
    status: 'live',
    live: true,
    belongings,
    sightings,
    alerts: makeAlerts(NOW),
    activity: makeActivity(NOW, sightings),
    now: new Date(NOW),
    apiBase: '',
    dismissing: new Set(),
    onDismissAlert: vi.fn(),
    ask: { entries: askEntries, pending: false, onAsk: vi.fn(), onClear: vi.fn() },
    ...overrides,
  };
  const utils = render(<Dashboard {...props} />);
  return { ...utils, props };
}

const card = (name: string) =>
  screen.getAllByTestId('belonging-card').find(c => within(c).queryByRole('heading', { name }))!;

describe('Dashboard', () => {
  it('renders header status and counts', () => {
    setup();
    expect(screen.getByRole('heading', { name: 'RecallAI' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Live');
    const counts = screen.getByLabelText('Counts');
    expect(within(counts).getByText('Belongings').nextSibling).toHaveTextContent('5');
    expect(within(counts).getByText('Sightings').nextSibling).toHaveTextContent('6'); // verified=0 excluded
    expect(within(counts).getByText('Alerts').nextSibling).toHaveTextContent('2');
  });

  it('shows connecting / disconnected states', () => {
    const { rerender, props } = setup({ status: 'connecting', belongings: [], sightings: [] });
    expect(screen.getByRole('status')).toHaveTextContent('Connecting');
    expect(screen.getByText(/Loading your belongings/)).toBeInTheDocument();
    rerender(<Dashboard {...props} status="disconnected" />);
    expect(screen.getByRole('status')).toHaveTextContent('Disconnected');
    expect(screen.getByText(/Can’t reach SpacetimeDB/)).toBeInTheDocument();
    rerender(<Dashboard {...props} status="live" />);
    expect(screen.getByText(/No belongings registered/)).toBeInTheDocument();
  });

  it('renders one card per belonging with the latest visible sighting', () => {
    setup();
    expect(screen.getAllByTestId('belonging-card')).toHaveLength(5);
    const laptop = card('My Laptop');
    // newest laptop row is verified=0 and must be hidden
    expect(within(laptop).queryByText(/Someone else/)).not.toBeInTheDocument();
    expect(within(laptop).getByText(/Open on the long hacking table/)).toBeInTheDocument();
    expect(within(laptop).getByText('Last seen 4 minutes ago')).toBeInTheDocument();
    expect(within(laptop).getByText('hacking table, row 3')).toBeInTheDocument();
    expect(within(laptop).getByText('cell phone')).toBeInTheDocument();
    expect(within(laptop).getByRole('img')).toHaveAttribute('src', '/demo/crop_laptop.jpg');

    expect(within(card('Water Bottle')).getByText('Left view')).toBeInTheDocument();
    expect(within(card('My Bag')).getByText('Unchecked')).toBeInTheDocument();
    expect(within(card('Phone')).getByText('Waiting for Grok…')).toBeInTheDocument();
    expect(within(card('My Mouse')).getByText('Not seen yet')).toBeInTheDocument();
  });

  it('falls back to the full frame when there is no crop', () => {
    const s = makeSighting({ id: 1n, belongingId: '365acc452f4e', seenAtMicros: micros(NOW), imageUrl: '/snapshots/f.jpg' });
    setup({ sightings: [s] });
    expect(within(card('My Mouse')).getByRole('img')).toHaveAttribute('src', '/snapshots/f.jpg');
  });

  it('updates a card live and highlights it', () => {
    vi.useFakeTimers();
    try {
      const { rerender, props } = setup();
      expect(card('My Mouse')).not.toHaveClass('card-flash');
      const fresh = makeSighting({
        id: 2000n, belongingId: '365acc452f4e', belongingName: 'My Mouse', seenAtMicros: micros(NOW),
        cropUrl: '/snapshots/crops/m.jpg', description: 'Next to the keyboard.',
      });
      rerender(<Dashboard {...props} sightings={[...props.sightings, fresh]} />);
      expect(within(card('My Mouse')).getByText('Next to the keyboard.')).toBeInTheDocument();
      expect(card('My Mouse')).toHaveClass('card-flash');
      act(() => vi.advanceTimersByTime(3000));
      expect(card('My Mouse')).not.toHaveClass('card-flash');
    } finally {
      vi.useRealTimers();
    }
  });

  it('ticks relative time when `now` changes', () => {
    const { rerender, props } = setup();
    expect(within(card('My Laptop')).getByText('Last seen 4 minutes ago')).toBeInTheDocument();
    rerender(<Dashboard {...props} now={new Date(NOW + 30 * 60_000)} />);
    expect(within(card('My Laptop')).getByText('Last seen 34 minutes ago')).toBeInTheDocument();
  });

  it('shows non-dismissed alerts newest first and dismisses by id', async () => {
    const { props } = setup();
    const alerts = within(screen.getByRole('region', { name: 'Alerts' })).getAllByRole('article');
    expect(alerts).toHaveLength(2);
    expect(alerts[0]).toHaveClass('alert-high');
    expect(alerts[0]).toHaveTextContent('Did you forget your water bottle?');
    expect(alerts[0]).toHaveTextContent('2 minutes ago');
    expect(within(alerts[0]).getByRole('img')).toHaveAttribute('src', '/demo/crop_bottle.jpg');
    expect(alerts[1]).toHaveClass('alert-low');
    expect(screen.queryByText(/must not render/)).not.toBeInTheDocument();
    await userEvent.click(within(alerts[0]).getByRole('button', { name: 'Dismiss' }));
    expect(props.onDismissAlert).toHaveBeenCalledWith(77n);
  });

  it('hides alerts that are being dismissed optimistically', () => {
    setup({ dismissing: new Set(['77']) });
    expect(within(screen.getByRole('region', { name: 'Alerts' })).getAllByRole('article')).toHaveLength(1);
  });

  it('opens a timeline drawer with visible sightings newest first, and a lightbox', async () => {
    setup();
    await userEvent.click(screen.getByRole('button', { name: 'Show sighting history for My Laptop' }));
    const drawer = screen.getByRole('dialog', { name: 'My Laptop' });
    const items = within(drawer).getAllByRole('listitem');
    expect(items).toHaveLength(2); // the verified=0 row is excluded
    expect(items[0]).toHaveTextContent('Open on the long hacking table');
    expect(items[1]).toHaveTextContent('Closed on the table');
    await userEvent.click(within(items[0]).getByRole('button', { name: /Open My Laptop/ }));
    const lb = screen.getByRole('dialog', { name: 'Full frame' });
    expect(within(lb).getByRole('img')).toHaveAttribute('src', '/demo/1792851254577_laptop.jpg');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Full frame' })).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'My Laptop' })).not.toBeInTheDocument();
  });

  it('renders answer citations as chips with live thumbnails and Grok steps', () => {
    setup();
    const chips = screen.getAllByTestId('citation');
    expect(chips.map(c => c.textContent)).toEqual(['#1031', '#1044']);
    expect(chips[1].querySelector('img')).toHaveAttribute('src', '/demo/crop_bottle.jpg');
    expect(screen.getByText('How Grok found this (3 steps)')).toBeInTheDocument();
    expect(screen.getByText(/recent_sightings \(belonging_id: b8e2d17c4a90, limit: 5\)/)).toBeInTheDocument();
  });

  it('uses the API row for citations missing from the live cache', () => {
    setup({
      ask: {
        entries: [{
          id: 9, question: 'q', status: 'done',
          response: { answer: 'Here [#5]. And [#6].', sightings: [{ id: 5, crop_path: 'snapshots/crops/5.jpg' }], steps: [] },
        }],
        pending: false, onAsk: vi.fn(), onClear: vi.fn(),
      },
    });
    const chips = screen.getAllByTestId('citation');
    expect(chips[0].querySelector('img')).toHaveAttribute('src', '/snapshots/crops/5.jpg');
    expect(chips[1].querySelector('img')).toBeNull();
    expect(chips[1]).toBeDisabled();
  });

  it('submits questions and shows pending / error states', async () => {
    const onAsk = vi.fn();
    const { rerender, props } = setup({ ask: { entries: [], pending: false, onAsk, onClear: vi.fn() } });
    await userEvent.type(screen.getByLabelText('Question for RecallAI'), 'where are my keys?{enter}');
    expect(onAsk).toHaveBeenCalledWith('where are my keys?');
    rerender(
      <Dashboard
        {...props}
        ask={{
          entries: [
            { id: 1, question: 'a', status: 'error', error: "Grok couldn't answer right now." },
            { id: 2, question: 'b', status: 'pending' },
          ],
          pending: true, onAsk, onClear: vi.fn(),
        }}
      />
    );
    expect(screen.getByRole('alert')).toHaveTextContent("Grok couldn't answer");
    expect(screen.getByText(/Grok is looking/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled();
  });

  it('lists live activity', () => {
    setup();
    const feed = screen.getByRole('region', { name: 'Live activity' });
    expect(within(feed).getByText('Phone spotted')).toBeInTheDocument();
    expect(within(feed).getByText('Grok described My Laptop')).toBeInTheDocument();
    expect(within(feed).getByText('Water Bottle left view')).toBeInTheDocument();
  });
});
