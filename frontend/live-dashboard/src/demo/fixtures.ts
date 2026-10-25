/** Realistic sample data for the demo page and component tests. */
import type { Alert, Belonging, Person, Sighting } from '../module_bindings/types';
import type { ActivityItem } from '../lib/activity';
import type { AskEntry } from '../components/types';

const MIN = 60_000;

/** Owner value (RecallAI user id as text) of every demo row: the signed-in demo user. */
export const DEMO_OWNER = '1';

export function micros(date: Date | number): bigint {
  const ms = typeof date === 'number' ? date : date.getTime();
  return BigInt(Math.round(ms)) * 1000n;
}

export function makeSighting(p: Partial<Sighting> & Pick<Sighting, 'id' | 'belongingId'>): Sighting {
  return {
    belongingName: '',
    objectName: '',
    event: 'seen',
    seenAtMicros: 0n,
    imageUrl: '',
    cropUrl: '',
    description: '',
    place: '',
    nearby: '',
    matchScore: 0.8,
    verified: 1,
    hasGps: false,
    latitude: 0,
    longitude: 0,
    owner: DEMO_OWNER,
    ...p,
  };
}

export const belongings: Belonging[] = [
  { id: 'a1f3c9e2b7d4', name: 'My Laptop', category: 'laptop', photos: 6, tracking: true, owner: DEMO_OWNER },
  { id: 'b8e2d17c4a90', name: 'Water Bottle', category: 'bottle', photos: 4, tracking: true, owner: DEMO_OWNER },
  { id: '3617519a583a', name: 'My Bag', category: 'backpack', photos: 5, tracking: true, owner: DEMO_OWNER },
  { id: 'c42f0e9d1b6a', name: 'Phone', category: 'cell phone', photos: 3, tracking: true, owner: DEMO_OWNER },
  { id: '365acc452f4e', name: 'My Mouse', category: 'mouse', photos: 2, tracking: true, owner: DEMO_OWNER },
];

/** A belonging whose enrollment is still collecting angles (info.json tracking: false). */
export const enrollingBelonging: Belonging = {
  id: '7fcd5eb42abe', name: 'Cup 1', category: 'cup', photos: 2, tracking: false, owner: DEMO_OWNER,
};

export function makePeople(now: number): Person[] {
  return [
    {
      id: 2n, name: 'Mom', description: '', relationship: 'family', knownSince: '',
      createdAtMicros: micros(now - 60 * MIN), updatedAtMicros: micros(now - 60 * MIN), owner: DEMO_OWNER,
    },
    {
      id: 1n, name: 'Chawin', description: 'Tall, glasses, CalHacks hoodie.', relationship: 'teammate',
      knownSince: '2024-09-01', createdAtMicros: micros(now - 120 * MIN), updatedAtMicros: micros(now - 5 * MIN), owner: DEMO_OWNER,
    },
  ];
}

export function makeSightings(now: number): Sighting[] {
  const img = (f: string) => `/demo/${f}`;
  return [
    // Laptop: newest row was rejected by Grok (verified 0) and must be hidden.
    makeSighting({
      id: 1051n, belongingId: 'a1f3c9e2b7d4', belongingName: 'My Laptop', objectName: 'laptop',
      seenAtMicros: micros(now - 1 * MIN), imageUrl: img('1792851263393_bottle.jpg'),
      cropUrl: img('crop_bag_red.jpg'), verified: 0, description: 'Someone else’s laptop on the far table.',
    }),
    makeSighting({
      id: 1048n, belongingId: 'a1f3c9e2b7d4', belongingName: 'My Laptop', objectName: 'laptop',
      seenAtMicros: micros(now - 4 * MIN), imageUrl: img('1792851254577_laptop.jpg'),
      cropUrl: img('crop_laptop.jpg'), place: 'hacking table, row 3',
      description: 'Open on the long hacking table, next to a water bottle and a tangle of chargers.',
      nearby: 'bottle, cell phone, backpack', matchScore: 0.91,
    }),
    makeSighting({
      id: 1012n, belongingId: 'a1f3c9e2b7d4', belongingName: 'My Laptop', objectName: 'laptop',
      seenAtMicros: micros(now - 95 * MIN), imageUrl: img('1792851263393_bottle.jpg'),
      cropUrl: img('crop_laptop.jpg'), place: 'hacking table, row 3',
      description: 'Closed on the table in front of the folding chairs.', nearby: 'bottle',
    }),
    // Bottle: left view 23 minutes ago (drives the high-urgency alert).
    makeSighting({
      id: 1044n, belongingId: 'b8e2d17c4a90', belongingName: 'Water Bottle', objectName: 'bottle',
      event: 'left_view', seenAtMicros: micros(now - 23 * MIN), imageUrl: img('1792851263393_bottle.jpg'),
      cropUrl: img('crop_bottle.jpg'), place: 'hacking table, row 3',
      description: 'Standing at the left edge of the table beside the laptop, just before it left the frame.',
      nearby: 'laptop, jacket',
    }),
    makeSighting({
      id: 1031n, belongingId: 'b8e2d17c4a90', belongingName: 'Water Bottle', objectName: 'bottle',
      seenAtMicros: micros(now - 41 * MIN), imageUrl: img('1792851254577_laptop.jpg'),
      cropUrl: img('crop_bottle.jpg'), place: 'hacking table, row 3',
      description: 'Upright on the table to the left of the open laptop.', nearby: 'laptop',
    }),
    // Bag: seen 2h ago, not yet verified by Grok.
    makeSighting({
      id: 998n, belongingId: '3617519a583a', belongingName: 'My Bag', objectName: 'backpack',
      seenAtMicros: micros(now - 128 * MIN), imageUrl: img('1792851254577_laptop.jpg'),
      cropUrl: img('crop_backpack.jpg'), verified: -1, place: 'back of chair',
      description: 'Hanging off the back of the white folding chair in the foreground.', nearby: 'chair, laptop',
    }),
    // Phone: just spotted, Grok hasn't described it yet.
    makeSighting({
      id: 1052n, belongingId: 'c42f0e9d1b6a', belongingName: 'Phone', objectName: 'cell phone',
      seenAtMicros: micros(now - 20_000), imageUrl: img('1792851268568_cell_phone.jpg'),
      cropUrl: img('crop_phone.jpg'), verified: -1,
    }),
  ];
}

/** What GET /api/live returns while the camera is on (boxes match /demo/1792851254577_laptop.jpg). */
export const demoLive = {
  online: true,
  age_seconds: 0.2,
  width: 640,
  height: 480,
  gps: false,
  in_view: [
    { belonging_id: 'a1f3c9e2b7d4', name: 'My Laptop', category: 'laptop', score: 0.912, bbox: [135, 292, 300, 358] },
    { belonging_id: '3617519a583a', name: 'My Bag', category: 'backpack', score: 0.861, bbox: [0, 352, 290, 480] },
  ],
  // Chawin was named on the feed before; the other two people aren't yet.
  people: [{ person_id: 1, name: 'Chawin', score: 0.83, bbox: [186, 222, 300, 305] }],
  detections: [
    { category: 'laptop', confidence: 0.88, bbox: [135, 292, 300, 358] },
    { category: 'backpack', confidence: 0.81, bbox: [0, 352, 290, 480] },
    { category: 'person', confidence: 0.9, bbox: [186, 222, 300, 305] },
    { category: 'person', confidence: 0.84, bbox: [62, 232, 112, 285] },
    { category: 'person', confidence: 0.71, bbox: [0, 240, 60, 330] },
    { category: 'bottle', confidence: 0.77, bbox: [63, 290, 98, 365] },
    { category: 'chair', confidence: 0.69, bbox: [430, 300, 580, 480] },
    { category: 'chair', confidence: 0.58, bbox: [240, 350, 330, 480] },
  ],
};

export function makeAlerts(now: number): Alert[] {
  return [
    {
      id: 77n, belongingId: 'b8e2d17c4a90', belongingName: 'Water Bottle', sightingId: 1044n,
      reason: 'left_behind', urgency: 'high',
      message: 'Did you forget your water bottle? It left view at the hacking table 23 minutes ago.',
      createdAtMicros: micros(now - 2 * MIN), dismissed: false, owner: DEMO_OWNER,
    },
    {
      id: 74n, belongingId: '3617519a583a', belongingName: 'My Bag', sightingId: 0n,
      reason: 'not_seen', urgency: 'low',
      message: 'Your bag hasn’t been seen in over 2 hours.',
      createdAtMicros: micros(now - 9 * MIN), dismissed: false, owner: DEMO_OWNER,
    },
    {
      id: 60n, belongingId: 'a1f3c9e2b7d4', belongingName: 'My Laptop', sightingId: 1012n,
      reason: 'left_behind', urgency: 'high', message: 'Old dismissed alert (must not render).',
      createdAtMicros: micros(now - 90 * MIN), dismissed: true, owner: DEMO_OWNER,
    },
  ];
}

export function makeActivity(now: number, sightings: Sighting[]): ActivityItem[] {
  const by = (id: bigint) => sightings.find(s => s.id === id)!;
  return [
    { key: 'a1', kind: 'new', sighting: by(1052n), receivedAt: now - 20_000 },
    { key: 'a2', kind: 'described', sighting: by(1048n), receivedAt: now - 3 * MIN },
    { key: 'a3', kind: 'new', sighting: by(1048n), receivedAt: now - 4 * MIN },
    { key: 'a4', kind: 'new', sighting: by(1044n), receivedAt: now - 23 * MIN },
  ];
}

export const askEntries: AskEntry[] = [
  {
    id: 1,
    question: 'Where did I leave my water bottle?',
    status: 'done',
    response: {
      answer:
        'Your water bottle was last on the hacking table in row 3, right next to your laptop [#1031]. ' +
        'It left the camera’s view about 23 minutes ago [#1044], so someone may have moved it, or you took it with you.',
      sightings: [],
      steps: [
        { tool: 'find_belonging', input: { name: 'water bottle' } },
        { tool: 'recent_sightings', input: { belonging_id: 'b8e2d17c4a90', limit: 5 } },
        { tool: 'look_at_photo', input: { sighting_id: 1044 } },
      ],
    },
  },
];
