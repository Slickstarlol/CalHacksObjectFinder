import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ipPlace, useIpLocation } from './useIpLocation';

describe('ipPlace', () => {
  it('formats city and region', () => {
    expect(ipPlace({ success: true, city: 'Irvine', region_code: 'CA', region: 'California' })).toBe('Irvine, CA');
    expect(ipPlace({ city: 'Berlin', region: 'Land Berlin' })).toBe('Berlin, Land Berlin');
    expect(ipPlace({ country_code: 'US' })).toBe('US');
  });
  it('is undefined for failed or empty answers', () => {
    expect(ipPlace({ success: false, city: 'x' })).toBeUndefined();
    expect(ipPlace({})).toBeUndefined();
    expect(ipPlace(null)).toBeUndefined();
  });
});

describe('useIpLocation', () => {
  it('looks the place up once', async () => {
    const f = vi.fn().mockResolvedValue(new Response(JSON.stringify({ success: true, city: 'Irvine', region_code: 'CA' })));
    const { result } = renderHook(() => useIpLocation(f, 'https://ip.test/'));
    await waitFor(() => expect(result.current).toBe('Irvine, CA'));
    expect(f).toHaveBeenCalledTimes(1);
    expect(f.mock.calls[0][0]).toBe('https://ip.test/');
  });
  it('stays undefined when the lookup fails', async () => {
    const f = vi.fn().mockRejectedValue(new Error('offline'));
    const { result } = renderHook(() => useIpLocation(f, 'https://ip.test/'));
    await waitFor(() => expect(f).toHaveBeenCalled());
    expect(result.current).toBeUndefined();
  });
});
