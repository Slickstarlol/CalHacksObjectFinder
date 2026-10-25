import { describe, expect, it } from 'vitest';
import { resolveUrl } from './url';

describe('resolveUrl', () => {
  it('keeps DB absolute paths same-origin by default', () => {
    expect(resolveUrl('/snapshots/1_x.jpg')).toBe('/snapshots/1_x.jpg');
  });
  it('adds a leading slash to agent-relative paths', () => {
    expect(resolveUrl('snapshots/crops/1.jpg')).toBe('/snapshots/crops/1.jpg');
  });
  it('prefixes the API base without doubling slashes', () => {
    expect(resolveUrl('/snapshots/a.jpg', 'http://localhost:8000/')).toBe('http://localhost:8000/snapshots/a.jpg');
  });
  it('leaves absolute URLs and empties alone', () => {
    expect(resolveUrl('https://x/y.jpg', 'http://b')).toBe('https://x/y.jpg');
    expect(resolveUrl('')).toBe('');
  });
});
