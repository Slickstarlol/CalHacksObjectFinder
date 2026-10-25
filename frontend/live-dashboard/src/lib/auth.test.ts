import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  AuthError,
  SESSION_KEY,
  fetchMe,
  loadSession,
  login,
  makeAuthFetch,
  mediaUrl,
  ownedBy,
  ownerKey,
  register,
  saveSession,
  setMediaToken,
  validateLogin,
  validateRegistration,
} from './auth';
import { resolveUrl } from './url';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const session = { token: 'tok123', user: { id: 4, username: 'kent', display_name: 'Kent' } };

afterEach(() => {
  localStorage.clear();
  setMediaToken(null);
});

describe('validateRegistration', () => {
  const ok = { username: 'kent_h', displayName: 'Kent', password: 'longenough', confirm: 'longenough' };
  it('accepts a valid form (username is normalised to lowercase)', () => {
    expect(validateRegistration(ok)).toEqual({});
    expect(validateRegistration({ ...ok, username: '  Kent.H-1 ' })).toEqual({});
  });
  it.each([
    ['ab', /3–32/],
    ['x'.repeat(33), /3–32/],
    ['kent h', /lowercase letters/],
    ['kent!', /lowercase letters/],
  ])('rejects username %j', (username, msg) => {
    expect(validateRegistration({ ...ok, username }).username).toMatch(msg);
  });
  it('requires 8+ character passwords that match', () => {
    expect(validateRegistration({ ...ok, password: 'short', confirm: 'short' }).password).toMatch(/at least 8/);
    expect(validateRegistration({ ...ok, confirm: 'different!' }).confirm).toMatch(/do not match/);
    expect(validateRegistration({ ...ok, displayName: 'x'.repeat(65) }).displayName).toMatch(/64/);
  });
  it('login needs both fields', () => {
    expect(validateLogin('', '')).toEqual({ username: expect.any(String), password: expect.any(String) });
    expect(validateLogin('kent', 'pw')).toEqual({});
  });
});

describe('session storage', () => {
  it('round-trips and ignores junk', () => {
    expect(loadSession()).toBeNull();
    saveSession(session);
    expect(loadSession()).toEqual(session);
    localStorage.setItem(SESSION_KEY, '{"token": 5}');
    expect(loadSession()).toBeNull();
    localStorage.setItem(SESSION_KEY, 'not json');
    expect(loadSession()).toBeNull();
    saveSession(session);
    saveSession(null);
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  });
});

describe('API calls', () => {
  it('login posts a normalised username and returns the session', async () => {
    const f = vi.fn().mockResolvedValue(json({ ...session, expires_at: 'x' }));
    await expect(login(' KENT ', 'pw-pw-pw-pw', '', f)).resolves.toEqual({ ...session, expires_at: 'x' });
    expect(f).toHaveBeenCalledWith('/api/auth/login', expect.objectContaining({ method: 'POST' }));
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ username: 'kent', password: 'pw-pw-pw-pw' });
  });
  it('register sends display_name and maps field errors', async () => {
    const f = vi
      .fn()
      .mockResolvedValueOnce(json(session, 201))
      .mockResolvedValueOnce(json({ detail: { message: 'That username is taken.', field: 'username' } }, 409));
    const form = { username: 'Kent', displayName: ' Kent H ', password: 'longenough', confirm: 'longenough' };
    await register(form, 'http://localhost:8000/', f);
    expect(f.mock.calls[0][0]).toBe('http://localhost:8000/api/auth/register');
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({
      username: 'kent', display_name: 'Kent H', password: 'longenough',
    });
    const err = await register(form, '', f).catch(e => e);
    expect(err).toBeInstanceOf(AuthError);
    expect(err).toMatchObject({ status: 409, field: 'username', message: 'That username is taken.' });
  });
  it('wrong password -> 401 AuthError with the server message', async () => {
    const f = vi.fn().mockResolvedValue(json({ detail: 'Incorrect username or password.' }, 401));
    await expect(login('kent', 'nope', '', f)).rejects.toMatchObject({
      status: 401, message: 'Incorrect username or password.',
    });
  });
  it('network failure explains how to start the server', async () => {
    const f = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(fetchMe('t', '', f)).rejects.toThrow(/port 8000/);
  });
  it('fetchMe sends the bearer token', async () => {
    const f = vi.fn().mockResolvedValue(json(session.user));
    await fetchMe('tok', '', f);
    expect(f.mock.calls[0][1].headers).toEqual({ Authorization: 'Bearer tok' });
  });
});

describe('makeAuthFetch', () => {
  it('adds the Authorization header and keeps other headers', async () => {
    const f = vi.fn().mockResolvedValue(json({}));
    const authed = makeAuthFetch('tok', vi.fn(), f);
    await authed('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
    const headers: Headers = f.mock.calls[0][1].headers;
    expect(headers.get('Authorization')).toBe('Bearer tok');
    expect(headers.get('Content-Type')).toBe('application/json');
  });
  it('reports a 401 so the app can return to sign in', async () => {
    const onUnauthorized = vi.fn();
    const f = vi.fn().mockResolvedValueOnce(json({}, 200)).mockResolvedValueOnce(json({}, 401));
    const authed = makeAuthFetch('tok', onUnauthorized, f);
    await authed('/api/alerts');
    expect(onUnauthorized).not.toHaveBeenCalled();
    const res = await authed('/api/alerts');
    expect(res.status).toBe(401);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });
});

describe('media URLs and owners', () => {
  it('adds ?token= only to snapshot and live-camera paths', () => {
    expect(mediaUrl('/snapshots/a.jpg', 't k')).toBe('/snapshots/a.jpg?token=t%20k');
    expect(mediaUrl('/api/live/frame.jpg?t=1', 'tok')).toBe('/api/live/frame.jpg?t=1&token=tok');
    expect(mediaUrl('http://localhost:8000/snapshots/a.jpg', 'tok')).toBe(
      'http://localhost:8000/snapshots/a.jpg?token=tok'
    );
    expect(mediaUrl('/demo/a.jpg', 'tok')).toBe('/demo/a.jpg');
    expect(mediaUrl('/snapshots/a.jpg', null)).toBe('/snapshots/a.jpg');
  });
  it('resolveUrl uses the signed-in token', () => {
    expect(resolveUrl('snapshots/x.jpg')).toBe('/snapshots/x.jpg');
    setMediaToken('abc');
    expect(resolveUrl('snapshots/x.jpg')).toBe('/snapshots/x.jpg?token=abc');
    expect(resolveUrl('/snapshots/x.jpg', 'http://h:8000')).toBe('http://h:8000/snapshots/x.jpg?token=abc');
    expect(resolveUrl('https://elsewhere/x.jpg')).toBe('https://elsewhere/x.jpg');
  });
  it('ownerKey / ownedBy', () => {
    expect(ownerKey({ id: 12 })).toBe('12');
    expect(ownerKey(null)).toBe('');
    const rows = [{ owner: '1', n: 1 }, { owner: '2', n: 2 }, { owner: '', n: 3 }];
    expect(ownedBy(rows, '1').map(r => r.n)).toEqual([1]);
    expect(ownedBy(rows, '')).toEqual([]); // signed-out never sees ownerless rows
  });
});
