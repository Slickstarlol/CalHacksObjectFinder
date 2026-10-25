/**
 * The Python camera's live view, as served by FastAPI GET /api/live
 * (webcam.py -> live_feed.LivePublisher -> POST /api/live).
 *
 * `in_view` = your recognized belongings (DINOv2 match score 0..1),
 * `detections` = every YOLO box (anonymous categories, confidence 0..1).
 * Boxes are [x1, y1, x2, y2] in pixels of the published preview frame.
 */

export type Box = [number, number, number, number];

export type LiveItem = {
  belongingId: string;
  name: string;
  category: string;
  /** DINOv2 similarity to your reference photos, 0..1. */
  score: number;
  bbox: Box | null;
};

export type LiveDetection = {
  category: string;
  confidence: number;
  bbox: Box | null;
};

export type LiveSnapshot = {
  online: boolean;
  ageSeconds: number | null;
  width: number | null;
  height: number | null;
  gps: boolean;
  inView: LiveItem[];
  detections: LiveDetection[];
};

export type CameraStatus = 'connecting' | 'online' | 'offline' | 'unreachable';

export type CameraState = LiveSnapshot & {
  status: CameraStatus;
  /** Latest frame URL ('' until one has loaded, or while offline). */
  frameUrl: string;
};

export const EMPTY_SNAPSHOT: LiveSnapshot = {
  online: false,
  ageSeconds: null,
  width: null,
  height: null,
  gps: false,
  inView: [],
  detections: [],
};

function num(v: unknown): number | null {
  const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
}

function box(v: unknown): Box | null {
  if (!Array.isArray(v) || v.length !== 4) return null;
  const b = v.map(num);
  if (b.some(x => x === null)) return null;
  const [x1, y1, x2, y2] = b as number[];
  return x2 > x1 && y2 > y1 ? [x1, y1, x2, y2] : null;
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : '';
}

/** Normalise whatever /api/live sent into a LiveSnapshot (never throws). */
export function parseLive(body: unknown): LiveSnapshot {
  if (!body || typeof body !== 'object') return EMPTY_SNAPSHOT;
  const b = body as Record<string, unknown>;
  const list = (v: unknown) => (Array.isArray(v) ? v.filter(x => x && typeof x === 'object') : []) as Record<string, unknown>[];
  const width = num(b.width);
  const height = num(b.height);
  return {
    online: b.online === true,
    ageSeconds: num(b.age_seconds),
    width: width && width > 0 ? width : null,
    height: height && height > 0 ? height : null,
    gps: b.gps === true,
    inView: list(b.in_view).map(i => ({
      belongingId: str(i.belonging_id),
      name: str(i.name) || 'Belonging',
      category: str(i.category),
      score: num(i.score) ?? 0,
      bbox: box(i.bbox),
    })),
    detections: list(b.detections).map(d => ({
      category: str(d.category) || 'object',
      confidence: num(d.confidence) ?? 0,
      bbox: box(d.bbox),
    })),
  };
}

/** A pixel box as CSS percentages of the frame, clamped to the frame. */
export function boxPercent(
  bbox: Box | null,
  width: number | null,
  height: number | null
): { left: number; top: number; width: number; height: number } | null {
  if (!bbox || !width || !height) return null;
  const clamp = (v: number, max: number) => Math.min(Math.max(v, 0), max);
  const x1 = clamp(bbox[0], width);
  const y1 = clamp(bbox[1], height);
  const x2 = clamp(bbox[2], width);
  const y2 = clamp(bbox[3], height);
  if (x2 <= x1 || y2 <= y1) return null;
  return {
    left: (x1 / width) * 100,
    top: (y1 / height) * 100,
    width: ((x2 - x1) / width) * 100,
    height: ((y2 - y1) / height) * 100,
  };
}

function iou(a: Box, b: Box): number {
  const ix = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0]));
  const iy = Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
  const inter = ix * iy;
  const area = (r: Box) => (r[2] - r[0]) * (r[3] - r[1]);
  const union = area(a) + area(b) - inter;
  return union > 0 ? inter / union : 0;
}

export type DetectionGroup = { category: string; count: number; confidence: number };

/**
 * YOLO detections that aren't one of your recognized belongings, grouped by
 * category (highest confidence first). A detection overlapping an in-view
 * belonging's box is the same object, so it's left out.
 */
export function otherDetections(detections: readonly LiveDetection[], inView: readonly LiveItem[]): DetectionGroup[] {
  const mine = inView.map(i => i.bbox).filter((b): b is Box => b !== null);
  const groups = new Map<string, DetectionGroup>();
  for (const d of detections) {
    if (d.bbox && mine.some(m => iou(m, d.bbox!) > 0.5)) continue;
    const g = groups.get(d.category);
    if (g) {
      g.count += 1;
      g.confidence = Math.max(g.confidence, d.confidence);
    } else {
      groups.set(d.category, { category: d.category, count: 1, confidence: d.confidence });
    }
  }
  return [...groups.values()].sort((a, b) => b.confidence - a.confidence);
}

/** Mean match score (0..100) of your belongings in view, or null if none are. */
export function feedAccuracy(inView: readonly LiveItem[]): number | null {
  if (inView.length === 0) return null;
  return (inView.reduce((sum, i) => sum + i.score, 0) / inView.length) * 100;
}

/** 0.913 -> "91%"; 91.3 (already a percent) works with asPercent. */
export function percent(score: number, digits = 0): string {
  return `${(Math.max(0, Math.min(1, score)) * 100).toFixed(digits)}%`;
}

/** Tone for an accuracy value: same thresholds the camera dashboard uses for match quality. */
export function accuracyTone(pct: number | null): 'good' | 'fair' | 'poor' | 'none' {
  if (pct === null) return 'none';
  return pct >= 85 ? 'good' : pct >= 70 ? 'fair' : 'poor';
}
