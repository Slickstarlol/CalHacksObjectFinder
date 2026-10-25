import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Dashboard, type DashboardProps } from './Dashboard';
import { parseLive, type Box, type CameraState } from '../lib/live';
import type { LabelResult } from '../lib/people';
import {
  askEntries,
  belongings,
  demoLive,
  enrollingBelonging,
  makeActivity,
  makeAlerts,
  makePeople,
  makeSighting,
  makeSightings,
  micros,
} from '../demo/fixtures';

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
    expect(screen.getByText(/Can’t reach the live database/)).toBeInTheDocument();
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
    expect(chips.map(c => c.querySelector('span')!.textContent)).toEqual(['#1031', '#1044']);
    expect(chips.map(c => c.querySelector('.cite-age')!.textContent)).toEqual(['41m', '23m']);
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

  describe('enrolling belongings (tracking = false)', () => {
    it('shows an Enrolling badge instead of "Not seen yet"', () => {
      setup({ belongings: [...belongings, enrollingBelonging] });
      const cup = card('Cup 1');
      expect(within(cup).getByText('2 angles saved')).toBeInTheDocument();
      expect(within(cup).getByText('Enrolling…', { selector: '.badge' })).toBeInTheDocument();
      expect(within(cup).queryByText('Not seen yet')).not.toBeInTheDocument();
      expect(within(cup).getByText(/from a few more angles/)).toBeInTheDocument();
      expect(within(cup).getByText('2 reference photos')).toBeInTheDocument();
      // tracked belongings never show the badge
      expect(within(card('My Mouse')).getByText('Not seen yet')).toBeInTheDocument();
      expect(within(card('My Mouse')).queryByText('Enrolling…')).not.toBeInTheDocument();
    });

    it('drops the badge once enrollment finishes', () => {
      const { rerender, props } = setup({ belongings: [enrollingBelonging] });
      expect(within(card('Cup 1')).getByText('Enrolling…', { selector: '.badge' })).toBeInTheDocument();
      rerender(<Dashboard {...props} belongings={[{ ...enrollingBelonging, photos: 5, tracking: true }]} />);
      expect(within(card('Cup 1')).queryByText('Enrolling…')).not.toBeInTheDocument();
      expect(within(card('Cup 1')).getByText('Not seen yet')).toBeInTheDocument();
    });
  });

  describe('filters', () => {
    it('counts belongings per state and filters the grid', async () => {
      setup({ belongings: [...belongings, enrollingBelonging] });
      const group = screen.getByRole('group', { name: 'Filter belongings' });
      const chip = (name: RegExp) => within(group).getByRole('button', { name });
      expect(chip(/^All/)).toHaveTextContent('All 6');
      expect(chip(/^Seen/)).toHaveTextContent('Seen 3');
      expect(chip(/^Left view/)).toHaveTextContent('Left view 1');
      expect(chip(/^Enrolling/)).toHaveTextContent('Enrolling 1');
      expect(chip(/^Not seen yet/)).toHaveTextContent('Not seen yet 1');

      await userEvent.click(chip(/^Left view/));
      expect(chip(/^Left view/)).toHaveAttribute('aria-pressed', 'true');
      expect(screen.getAllByTestId('belonging-card')).toHaveLength(1);
      expect(card('Water Bottle')).toBeInTheDocument();

      await userEvent.click(chip(/^All/));
      expect(screen.getAllByTestId('belonging-card')).toHaveLength(6);
    });

    it('shows the left-behind timer once an item has been out of view long enough', () => {
      setup({ leftAfter: 15 });
      expect(within(card('Water Bottle')).getByText('Left behind for 23 minutes')).toBeInTheDocument();
      expect(within(card('My Laptop')).queryByText(/Left behind/)).not.toBeInTheDocument();
    });
  });

  describe('live feed', () => {
    const online: CameraState = { ...parseLive(demoLive), status: 'online', frameUrl: '/api/live/frame.jpg?t=1' };

    it('shows the camera frame with a clickable box per belonging in view', async () => {
      setup({ camera: online });
      const panel = screen.getByRole('region', { name: /Live feed/ });
      expect(within(panel).getByText('REC')).toBeInTheDocument();
      expect(within(panel).getByRole('img', { name: /In view: My Laptop, My Bag, Chawin/ })).toHaveAttribute(
        'src',
        '/api/live/frame.jpg?t=1'
      );
      const box = within(panel).getByRole('button', { name: 'My Laptop in view, 91% match. Open its history' });
      expect(box).toHaveStyle({ left: `${(135 / 640) * 100}%`, width: `${((300 - 135) / 640) * 100}%` });
      await userEvent.click(box);
      expect(screen.getByRole('dialog', { name: 'My Laptop' })).toBeInTheDocument();
    });

    it('lists what is happening: yours in view, missing items, each person and other objects', () => {
      setup({ camera: online });
      const panel = screen.getByRole('region', { name: /Live feed/ });
      const rows = within(panel).getAllByRole('listitem').filter(li => li.classList.contains('event'));
      const text = rows.map(r => r.textContent);
      expect(text[0]).toMatch(/^In viewMy Laptop.*91\.2%/);
      expect(text[1]).toMatch(/^In viewMy Bag.*86\.1%/);
      expect(text[2]).toMatch(/^MissingWater BottleOut of view since/);
      expect(text[3]).toMatch(/^PersonChawinRecognized from the photos you named.*83\.0%/);
      // One row per unnamed person, numbered left to right.
      expect(text[4]).toMatch(/^PersonPerson 1Detected, not identified.*71\.0%/);
      expect(text[5]).toMatch(/^PersonPerson 2Detected, not identified.*84\.0%/);
      // No room left: the other objects share a row (laptop/backpack are your belongings, so not repeated).
      expect(text[6]).toMatch(/^Objectsbottle, 2 × chairNot your belongings/);
      expect(text).toHaveLength(7);
      expect(within(panel).getByText('8 tracked · 3 people')).toBeInTheDocument();
      expect(within(panel).getByText('match 88.7%')).toBeInTheDocument();
    });

    it('names a person by double-clicking their row; the picture only shows names', async () => {
      let resolve!: (r: LabelResult) => void;
      const onLabelPerson = vi.fn((_bbox: Box, _name: string) => new Promise<LabelResult>(r => (resolve = r)));
      const { rerender, props } = setup({ camera: online, people: makePeople(NOW), onLabelPerson, onRenamePerson: vi.fn() });
      const panel = screen.getByRole('region', { name: /Live feed/ });
      const scene = panel.querySelector<HTMLElement>('.live-scene')!;
      // Nothing on the camera picture is editable: its only buttons are your belongings.
      expect(within(scene).getByText('Chawin 83%')).toBeInTheDocument();
      expect(within(scene).queryByText(/Who\?|Person \d/)).toBeNull();
      for (const b of within(scene).getAllByRole('button')) expect(b).toHaveAccessibleName(/Open its history$/);

      const personRow = () =>
        within(panel).getByRole('button', { name: 'Person 2 in view, not named yet. Press Enter to name them' });
      // Hovering a row outlines that person on the picture.
      await userEvent.hover(personRow());
      expect(within(scene).getByText('Person 2')).toBeInTheDocument();
      expect(scene.querySelector('.is-focus')).toHaveStyle({ left: `${(62 / 640) * 100}%` });

      await userEvent.dblClick(personRow());
      await userEvent.keyboard('{Escape}');
      expect(within(panel).queryByRole('textbox')).toBeNull();
      expect(onLabelPerson).not.toHaveBeenCalled();

      await userEvent.dblClick(personRow());
      await userEvent.type(within(panel).getByRole('textbox', { name: 'Name Person 2' }), 'Maya{Enter}');
      expect(onLabelPerson).toHaveBeenCalledWith([62, 232, 112, 285], 'Maya');
      await act(async () => resolve({ personId: 9, name: 'Maya', created: true, bbox: [62, 232, 112, 285] }));
      expect(within(scene).getByText('Maya · learning…')).toBeInTheDocument();
      expect(within(panel).getByRole('button', { name: 'Maya in view, press Enter to rename' })).toHaveTextContent(
        'Learning… the camera is saving a photo'
      );

      const maya = { personId: 9, name: 'Maya', score: 0.8, bbox: [62, 232, 112, 285] as Box };
      rerender(<Dashboard {...props} camera={{ ...online, people: [...online.people, maya] }} />);
      expect(within(scene).getByText('Maya 80%')).toBeInTheDocument();
      expect(within(panel).getByRole('button', { name: 'Maya in view, press Enter to rename' })).toHaveTextContent(
        'Recognized from the photos you named'
      );
      expect(within(panel).getAllByRole('button', { name: /not named yet/ })).toHaveLength(1);
    });

    it('renames a recognized person from their row, or relabels them as another of your people', async () => {
      const onRenamePerson = vi.fn(() => Promise.resolve());
      const onLabelPerson = vi.fn(async (bbox: Box, name: string, personId?: number) => ({
        personId: personId!,
        name,
        created: false,
        bbox,
      }));
      setup({ camera: online, people: makePeople(NOW), onLabelPerson, onRenamePerson });
      const panel = screen.getByRole('region', { name: /Live feed/ });
      within(panel).getByRole('button', { name: 'Chawin in view, press Enter to rename' }).focus();
      await userEvent.keyboard('{Enter}');
      const input = within(panel).getByRole('textbox', { name: 'New name for Chawin' });
      await userEvent.clear(input);
      await userEvent.type(input, 'Chawin M.{Enter}');
      expect(onRenamePerson).toHaveBeenCalledWith(expect.objectContaining({ id: 1n }), 'Chawin M.');
      const renamed = within(panel).getByRole('button', { name: 'Chawin M. in view, press Enter to rename' });

      // "mom" is another of your people: that box is Mom, Chawin keeps his name.
      await userEvent.dblClick(renamed);
      const again = within(panel).getByRole('textbox', { name: 'New name for Chawin M.' });
      await userEvent.clear(again);
      await userEvent.type(again, 'mom{Enter}');
      expect(onLabelPerson).toHaveBeenCalledWith([186, 222, 300, 305], 'Mom', 2);
      expect(onRenamePerson).toHaveBeenCalledTimes(1);
      expect(await within(panel).findByText('Mom · learning…')).toBeInTheDocument();
    });

    it('says why a name was not saved, and is read-only without a handler', async () => {
      const onLabelPerson = vi.fn(() => Promise.reject(new Error('That person isn’t in view any more.')));
      const { unmount } = setup({ camera: online, people: makePeople(NOW), onLabelPerson });
      let panel = screen.getByRole('region', { name: /Live feed/ });
      await userEvent.dblClick(within(panel).getByRole('button', { name: /^Person 1 in view/ }));
      await userEvent.type(within(panel).getByRole('textbox', { name: 'Name Person 1' }), 'Sam{Enter}');
      expect(await within(panel).findByRole('alert')).toHaveTextContent('That person isn’t in view any more.');
      expect(within(panel).queryByText(/Sam/)).toBeNull();
      unmount();

      setup({ camera: online, people: makePeople(NOW) });
      panel = screen.getByRole('region', { name: /Live feed/ });
      expect(within(panel).queryByRole('button', { name: /not named yet|to rename/ })).toBeNull();
      expect(within(panel).getByText('Person 1')).toBeInTheDocument();
      expect(within(panel).getByText('Chawin 83%')).toBeInTheDocument();
    });

    it('explains how to start the camera when it is off', () => {
      setup({ camera: { ...parseLive({ online: false }), status: 'offline', frameUrl: '' } });
      const panel = screen.getByRole('region', { name: /Live feed/ });
      expect(within(panel).getByText('OFF')).toBeInTheDocument();
      expect(within(panel).getByText('python -m recallai.vision.webcam')).toBeInTheDocument();
      expect(within(panel).queryByRole('img')).not.toBeInTheDocument();
    });
  });

  it('flips light and dark mode from the top bar and remembers the choice', async () => {
    const root = document.documentElement;
    root.removeAttribute('data-theme');
    try {
      setup();
      await userEvent.click(screen.getByRole('button', { name: 'Switch to dark mode' }));
      expect(root.dataset.theme).toBe('dark');
      expect(localStorage.getItem('recallai.theme')).toBe('dark');
      await userEvent.click(screen.getByRole('button', { name: 'Switch to light mode' }));
      expect(root.dataset.theme).toBe('light');
      expect(localStorage.getItem('recallai.theme')).toBe('light');
    } finally {
      root.removeAttribute('data-theme');
      localStorage.removeItem('recallai.theme');
    }
  });

  describe('people panel', () => {
    it('lists people alphabetically with relationship and how long known', () => {
      setup({ people: makePeople(NOW) });
      const panel = screen.getByRole('region', { name: 'People' });
      const rows = within(panel).getAllByTestId('person');
      expect(rows.map(r => within(r).getByText(/^(Chawin|Mom)$/).textContent)).toEqual(['Chawin', 'Mom']);
      expect(within(rows[0]).getByText('teammate')).toBeInTheDocument();
      expect(within(rows[0]).getByText(/^Known for 2 years and 1 month$/)).toBeInTheDocument();
      expect(within(rows[0]).getByText('Tall, glasses, CalHacks hoodie.')).toBeInTheDocument();
      expect(within(rows[1]).queryByText(/Known for/)).not.toBeInTheDocument();
    });

    it('renames a person on double-click and shows the new name right away', async () => {
      let resolve!: () => void;
      const onRenamePerson = vi.fn(() => new Promise<void>(r => (resolve = r)));
      const { rerender, props } = setup({ people: makePeople(NOW), onRenamePerson });
      const panel = screen.getByRole('region', { name: 'People' });
      await userEvent.dblClick(within(panel).getByRole('button', { name: /^Chawin, press Enter to rename/ }));
      const input = within(panel).getByRole('textbox', { name: 'New name for Chawin' });
      await userEvent.clear(input);
      await userEvent.type(input, 'Chawin M.{Enter}');
      expect(onRenamePerson).toHaveBeenCalledWith(expect.objectContaining({ id: 1n }), 'Chawin M.');
      expect(within(panel).getByText('Chawin M.')).toBeInTheDocument(); // before SpacetimeDB echoes it
      await act(async () => resolve());
      // the bridge mirrors the row back; the pending name gives way to the real one
      rerender(<Dashboard {...props} people={makePeople(NOW).map(p => (p.id === 1n ? { ...p, name: 'Chawin M.' } : p))} />);
      expect(within(panel).getByText('Chawin M.')).toBeInTheDocument();
    });

    it('Escape cancels, an unchanged name saves nothing, and a failed save reverts', async () => {
      const onRenamePerson = vi.fn().mockRejectedValue(new Error('Couldn’t save the name: Name is required'));
      setup({ people: makePeople(NOW), onRenamePerson });
      const panel = screen.getByRole('region', { name: 'People' });
      const mom = () => within(panel).getByRole('button', { name: /^Mom, press Enter to rename/ });

      await userEvent.dblClick(mom());
      await userEvent.type(within(panel).getByRole('textbox'), ' and Dad{Escape}');
      await userEvent.dblClick(mom());
      await userEvent.type(within(panel).getByRole('textbox'), '{Enter}');
      expect(onRenamePerson).not.toHaveBeenCalled();

      mom().focus();
      await userEvent.keyboard('{Enter}');
      await userEvent.type(within(panel).getByRole('textbox'), '!{Enter}');
      expect(onRenamePerson).toHaveBeenCalledWith(expect.objectContaining({ id: 2n }), 'Mom!');
      expect(await within(panel).findByRole('alert')).toHaveTextContent('Name is required');
      expect(mom()).toHaveTextContent(/^Mom$/);
    });

    it('shows an empty state', () => {
      setup({ people: [] });
      expect(screen.getByText('No people saved yet.')).toBeInTheDocument();
    });
  });
});
