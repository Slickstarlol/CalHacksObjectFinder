import { describe, expect, it } from 'vitest';
import { PERSON_COLORS, personColor, personColorVars } from './personColor';

describe('personColor', () => {
  it('gives neighbouring ids different colors and the same id the same one', () => {
    const rings = [1, 2, 3, 4, 5, 6, 7, 8].map(id => personColor(id)!.ring);
    expect(new Set(rings).size).toBe(PERSON_COLORS.length);
    expect(personColor(3)).toEqual(personColor('3'));
    expect(personColor(3n)).toEqual(personColor(3));
    expect(personColor(3 + PERSON_COLORS.length)).toEqual(personColor(3));
  });
  it('leaves unnamed people on the default color', () => {
    expect(personColor(undefined)).toBeUndefined();
    expect(personColorVars(undefined)).toEqual({});
    expect(personColorVars(1)).toEqual({ '--pc': PERSON_COLORS[1].ring, '--pc-tag': PERSON_COLORS[1].tag });
  });
});
