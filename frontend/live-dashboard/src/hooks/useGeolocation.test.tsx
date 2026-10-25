import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useGeolocation } from './useGeolocation';

type Success = (pos: { coords: { latitude: number; longitude: number; accuracy: number } }) => void;

function fakeGeo() {
  const watchers: Success[] = [];
  const geo = {
    watchPosition: vi.fn((ok: Success) => watchers.push(ok) - 1),
    clearWatch: vi.fn(),
  };
  Object.defineProperty(navigator, 'geolocation', { value: geo, configurable: true });
  const fix = (latitude: number, longitude: number) => watchers.forEach(w => w({ coords: { latitude, longitude, accuracy: 12 } }));
  return { geo, fix };
}

afterEach(() => {
  vi.useRealTimers();
  Object.defineProperty(navigator, 'geolocation', { value: undefined, configurable: true });
});

describe('useGeolocation', () => {
  it('asks for location on open and posts fixes to /api/gps, throttled', () => {
    vi.useFakeTimers();
    const { geo, fix } = fakeGeo();
    const f = vi.fn().mockResolvedValue(new Response('{}'));
    const { unmount } = renderHook(() => useGeolocation('http://api:8000/', f, { minIntervalMs: 10_000 }));
    expect(geo.watchPosition).toHaveBeenCalledTimes(1);

    fix(37.87, -122.26);
    fix(37.88, -122.27); // too soon: skipped
    expect(f).toHaveBeenCalledTimes(1);
    expect(f.mock.calls[0][0]).toBe('http://api:8000/api/gps');
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ latitude: 37.87, longitude: -122.26, accuracy: 12 });

    vi.advanceTimersByTime(10_000);
    fix(37.89, -122.28);
    expect(f).toHaveBeenCalledTimes(2);

    unmount();
    expect(geo.clearWatch).toHaveBeenCalledWith(0);
  });

  it('does nothing without geolocation', () => {
    const f = vi.fn();
    renderHook(() => useGeolocation('', f));
    expect(f).not.toHaveBeenCalled();
  });
});
