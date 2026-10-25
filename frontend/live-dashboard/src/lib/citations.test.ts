import { describe, expect, it } from 'vitest';
import { citedIds, normalizeId, parseAnswer } from './citations';

describe('parseAnswer', () => {
  it('splits text and [#id] citations', () => {
    expect(parseAnswer('On the desk [#12]. Earlier [#7] too')).toEqual([
      { kind: 'text', text: 'On the desk ' },
      { kind: 'cite', id: '12' },
      { kind: 'text', text: '. Earlier ' },
      { kind: 'cite', id: '7' },
      { kind: 'text', text: ' too' },
    ]);
  });
  it('handles adjacent and grouped citations', () => {
    expect(parseAnswer('[#1][#2]').map(s => s.kind)).toEqual(['cite', 'cite']);
    expect(parseAnswer('x [#3, #4]')).toEqual([
      { kind: 'text', text: 'x ' },
      { kind: 'cite', id: '3' },
      { kind: 'cite', id: '4' },
    ]);
  });
  it('ignores non-citation brackets and keeps plain text', () => {
    expect(parseAnswer('see [note] and #5')).toEqual([{ kind: 'text', text: 'see [note] and #5' }]);
    expect(parseAnswer('')).toEqual([]);
  });
  it('preserves u64 ids beyond Number precision', () => {
    expect(parseAnswer('[#18446744073709551615]')).toEqual([{ kind: 'cite', id: '18446744073709551615' }]);
  });
});

describe('citedIds / normalizeId', () => {
  it('dedupes in order and normalises leading zeros', () => {
    expect(citedIds('[#5] a [#007] b [#5]')).toEqual(['5', '7']);
    expect(normalizeId('0042')).toBe('42');
    expect(String(42n)).toBe(normalizeId('42'));
  });
});
