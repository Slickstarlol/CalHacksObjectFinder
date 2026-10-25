import { describe, expect, it } from 'vitest';
import {
  accuracyTone,
  boxPercent,
  feedAccuracy,
  otherDetections,
  parseLive,
  percent,
  unnamedPeople,
  type LiveDetection,
} from './live';

describe('parseLive', () => {
  it('maps the /api/live body to camelCase and keeps only valid boxes', () => {
    const snap = parseLive({
      online: true,
      age_seconds: 0.4,
      width: 640,
      height: 480,
      gps: true,
      in_view: [
        { belonging_id: 'abc', name: 'My Bag', category: 'backpack', score: 0.86, bbox: [0, 10, 100, 200] },
        { belonging_id: 'def', name: '', score: '0.7', bbox: [5, 5, 1, 1] },
        'junk',
      ],
      people: [
        { person_id: 4, name: 'Maya', score: 0.81, bbox: [10, 20, 60, 200] },
        { person_id: 'x', name: 'No id', score: 0.9, bbox: [0, 0, 5, 5] },
      ],
      detections: [{ category: 'person', confidence: 0.9, bbox: [1, 2, 3] }],
    });
    expect(snap).toEqual({
      online: true,
      ageSeconds: 0.4,
      width: 640,
      height: 480,
      gps: true,
      inView: [
        { belongingId: 'abc', name: 'My Bag', category: 'backpack', score: 0.86, bbox: [0, 10, 100, 200] },
        { belongingId: 'def', name: 'Belonging', category: '', score: 0.7, bbox: null },
      ],
      people: [{ personId: 4, name: 'Maya', score: 0.81, bbox: [10, 20, 60, 200] }],
      detections: [{ category: 'person', confidence: 0.9, bbox: null }],
    });
  });

  it('treats anything unexpected as an offline camera', () => {
    for (const body of [null, 'x', 42, { online: 'yes', width: -1 }]) {
      const snap = parseLive(body);
      expect(snap.online).toBe(false);
      expect(snap.width).toBeNull();
      expect(snap.inView).toEqual([]);
    }
  });
});

describe('boxPercent', () => {
  it('converts pixels to percentages and clamps to the frame', () => {
    expect(boxPercent([64, 48, 320, 240], 640, 480)).toEqual({ left: 10, top: 10, width: 40, height: 40 });
    expect(boxPercent([-20, 400, 700, 600], 640, 480)).toEqual({
      left: 0,
      top: (400 / 480) * 100,
      width: 100,
      height: (80 / 480) * 100,
    });
  });

  it('returns null without a box or frame size', () => {
    expect(boxPercent(null, 640, 480)).toBeNull();
    expect(boxPercent([0, 0, 10, 10], null, 480)).toBeNull();
    expect(boxPercent([700, 0, 800, 10], 640, 480)).toBeNull(); // entirely outside
  });
});

describe('otherDetections', () => {
  it('drops detections that are a recognized belonging and groups the rest', () => {
    const inView = [{ belongingId: 'a', name: 'Laptop', category: 'laptop', score: 0.9, bbox: [0, 0, 100, 100] as const }];
    const groups = otherDetections(
      [
        { category: 'laptop', confidence: 0.8, bbox: [2, 2, 98, 101] },
        { category: 'chair', confidence: 0.5, bbox: [200, 0, 300, 100] },
        { category: 'chair', confidence: 0.6, bbox: [300, 0, 400, 100] },
        { category: 'person', confidence: 0.9, bbox: null },
      ],
      inView.map(i => ({ ...i, bbox: [...i.bbox] as [number, number, number, number] }))
    );
    expect(groups).toEqual([
      { category: 'person', count: 1, confidence: 0.9 },
      { category: 'chair', count: 2, confidence: 0.6 },
    ]);
  });
});

describe('scores', () => {
  it('averages match scores of items in view', () => {
    expect(feedAccuracy([])).toBeNull();
    const item = (score: number) => ({ belongingId: '', name: '', category: '', score, bbox: null });
    expect(feedAccuracy([item(0.9), item(0.8)])).toBeCloseTo(85);
  });

  it('formats and grades percentages', () => {
    expect(percent(0.913)).toBe('91%');
    expect(percent(1.4)).toBe('100%');
    expect(accuracyTone(null)).toBe('none');
    expect(accuracyTone(90)).toBe('good');
    expect(accuracyTone(75)).toBe('fair');
    expect(accuracyTone(40)).toBe('poor');
  });
});

describe('people on the feed', () => {
  const dets: LiveDetection[] = [
    { category: 'person', confidence: 0.9, bbox: [10, 20, 60, 200] },
    { category: 'person', confidence: 0.7, bbox: [300, 20, 360, 200] },
    { category: 'chair', confidence: 0.6, bbox: [100, 100, 200, 200] },
  ];
  const maya = { personId: 4, name: 'Maya', score: 0.8, bbox: [12, 22, 60, 198] as [number, number, number, number] };

  it('finds the person boxes without a name', () => {
    expect(unnamedPeople(dets, [maya])).toEqual([dets[1]]);
    expect(unnamedPeople(dets, [])).toEqual([dets[0], dets[1]]);
  });

  it('leaves named people out of the anonymous groups', () => {
    expect(otherDetections(dets, [], [maya])).toEqual([
      { category: 'person', count: 1, confidence: 0.7 },
      { category: 'chair', count: 1, confidence: 0.6 },
    ]);
  });
});
