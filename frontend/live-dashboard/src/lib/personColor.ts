import type { CSSProperties } from 'react';

/**
 * One color per person, the same on their live-image box and their People
 * avatar. Skips the accent blue (your belongings) and the focus yellow
 * (naming someone). `tag` is the dark backing behind their name on the image.
 */
export const PERSON_COLORS: readonly { ring: string; tag: string }[] = [
  { ring: '#9b87ff', tag: '#2a1f6b' }, // violet
  { ring: '#ff6fb5', tag: '#5c1739' }, // pink
  { ring: '#ff9a3c', tag: '#5a2e06' }, // orange
  { ring: '#2ed3c6', tag: '#0b4441' }, // teal
  { ring: '#ff5a5a', tag: '#5c1414' }, // red
  { ring: '#9be15d', tag: '#2c4a12' }, // lime
  { ring: '#e15ff0', tag: '#4d1655' }, // magenta
  { ring: '#d4a373', tag: '#4a3320' }, // tan
];

/** A person's color by id; undefined for someone not named yet. */
export function personColor(id: number | bigint | string | undefined | null) {
  if (id === undefined || id === null || id === '') return undefined;
  const n = Number(id);
  if (!Number.isFinite(n)) return undefined;
  return PERSON_COLORS[Math.abs(Math.trunc(n)) % PERSON_COLORS.length];
}

/** Inline CSS variables (--pc, --pc-tag) the person styles read. */
export function personColorVars(id: Parameters<typeof personColor>[0]): CSSProperties {
  const c = personColor(id);
  return c ? ({ '--pc': c.ring, '--pc-tag': c.tag } as CSSProperties) : {};
}
