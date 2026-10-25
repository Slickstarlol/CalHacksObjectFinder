import { describe, expect, it } from 'vitest';
import { defaultSpacetimeHost } from './host';

describe('defaultSpacetimeHost', () => {
  it('uses the host that served the page', () => {
    expect(defaultSpacetimeHost({ hostname: 'localhost', protocol: 'http:' })).toBe('ws://localhost:3000');
    expect(defaultSpacetimeHost({ hostname: '192.168.1.42', protocol: 'http:' })).toBe('ws://192.168.1.42:3000');
    expect(defaultSpacetimeHost({ hostname: 'demo.example', protocol: 'https:' })).toBe('wss://demo.example:3000');
    expect(defaultSpacetimeHost({ hostname: '::1', protocol: 'http:' })).toBe('ws://[::1]:3000');
    expect(defaultSpacetimeHost(undefined)).toBe('ws://localhost:3000');
  });
});
