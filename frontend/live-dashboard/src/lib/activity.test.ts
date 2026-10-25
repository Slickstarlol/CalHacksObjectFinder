import { describe, expect, it } from 'vitest';
import { makeSighting } from '../demo/fixtures';
import { activityText, classifyUpdate, pushActivity } from './activity';

const a = makeSighting({ id: 1n, belongingId: 'x', belongingName: 'Bag' });

describe('activity feed', () => {
  it('classifies a description arriving as "described"', () => {
    expect(classifyUpdate(a, { ...a, description: 'on the chair' })).toBe('described');
    expect(classifyUpdate({ ...a, description: 'x' }, { ...a, description: 'y' })).toBe('updated');
  });
  it('prepends, collapses the same sighting, and caps length', () => {
    let feed = pushActivity([], 'new', a, 1);
    feed = pushActivity(feed, 'described', { ...a, description: 'd' }, 2);
    expect(feed).toHaveLength(1);
    expect(feed[0].kind).toBe('described');
    for (let i = 2; i < 20; i++) feed = pushActivity(feed, 'new', { ...a, id: BigInt(i) }, i, 10);
    expect(feed).toHaveLength(10);
    expect(feed[0].sighting.id).toBe(19n);
  });
  it('describes items', () => {
    expect(activityText({ key: 'k', kind: 'new', sighting: a, receivedAt: 0 })).toBe('Bag spotted');
    expect(activityText({ key: 'k', kind: 'new', sighting: { ...a, event: 'left_view' }, receivedAt: 0 })).toBe('Bag left view');
    expect(activityText({ key: 'k', kind: 'described', sighting: a, receivedAt: 0 })).toBe('Grok described Bag');
  });
});
