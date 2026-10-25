/** Parsing Grok answers that cite sightings as "[#123]" (also tolerates "[#1, #2]"). */

export type AnswerSegment =
  | { kind: 'text'; text: string }
  | { kind: 'cite'; id: string };

const CITE_GROUP = /\[\s*(#\d+(?:\s*,\s*#?\d+)*)\s*\]/g;

/** Split an answer into text and citation segments. Ids are decimal strings (u64-safe). */
export function parseAnswer(answer: string): AnswerSegment[] {
  const out: AnswerSegment[] = [];
  let last = 0;
  for (const m of answer.matchAll(CITE_GROUP)) {
    const start = m.index ?? 0;
    if (start > last) out.push({ kind: 'text', text: answer.slice(last, start) });
    for (const id of m[1].match(/\d+/g) ?? []) out.push({ kind: 'cite', id: normalizeId(id) });
    last = start + m[0].length;
  }
  if (last < answer.length) out.push({ kind: 'text', text: answer.slice(last) });
  return out;
}

/** Unique cited ids in order of first appearance. */
export function citedIds(answer: string): string[] {
  const seen = new Set<string>();
  for (const seg of parseAnswer(answer)) if (seg.kind === 'cite') seen.add(seg.id);
  return [...seen];
}

/** "007" -> "7" so ids match String(bigint). */
export function normalizeId(id: string): string {
  try {
    return BigInt(id).toString();
  } catch {
    return id;
  }
}
