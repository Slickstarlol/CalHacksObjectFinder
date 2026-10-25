/** Auth gate: sign-in / register screen until signed in, then the dashboard. */
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SESSION_KEY, getMediaToken, saveSession } from './lib/auth';

// The dashboard itself is covered by App.test; here it's a stub that shows
// the props the gate hands it, and can trigger an API 401.
const appProps: { authFetch?: typeof fetch } = {};
vi.mock('./App', () => ({
  default: (p: { user: { username: string }; authFetch: typeof fetch; onSignOut: () => void }) => {
    appProps.authFetch = p.authFetch;
    return (
      <div>
        <p>Dashboard for {p.user.username}</p>
        <button onClick={p.onSignOut}>Sign out</button>
      </div>
    );
  },
}));
const providerMounts = vi.fn();
vi.mock('spacetimedb/react', () => ({
  SpacetimeDBProvider: ({ children }: { children: React.ReactNode }) => {
    providerMounts();
    return <>{children}</>;
  },
}));

import Root from './Root';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const user = { id: 3, username: 'kent', display_name: 'Kent' };

afterEach(() => {
  localStorage.clear();
  providerMounts.mockClear();
});

function renderRoot(fetchImpl: typeof fetch) {
  return render(<Root connectionBuilder={{} as never} apiBase="" fetchImpl={fetchImpl} />);
}

describe('auth gate', () => {
  it('signed out: only the sign-in screen, no SpacetimeDB connection', () => {
    renderRoot(vi.fn());
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.queryByText(/Dashboard for/)).not.toBeInTheDocument();
    expect(providerMounts).not.toHaveBeenCalled();
  });

  it('register -> dashboard -> sign out -> sign in again', async () => {
    const f = vi
      .fn()
      .mockResolvedValueOnce(json({ token: 'reg-token', user }, 201)) // register
      .mockResolvedValueOnce(json({ status: 'signed out' })) // logout
      .mockResolvedValueOnce(json({ token: 'login-token', user })); // login
    renderRoot(f);
    await userEvent.click(screen.getByRole('button', { name: 'Create an account' }));
    expect(screen.getByRole('heading', { name: 'Create your account' })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('Username'), 'Kent');
    await userEvent.type(screen.getByLabelText(/Display name/), 'Kent');
    await userEvent.type(screen.getByLabelText('Password'), 'longenough');
    await userEvent.type(screen.getByLabelText('Confirm password'), 'longenough');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));

    expect(await screen.findByText('Dashboard for kent')).toBeInTheDocument();
    expect(JSON.parse(f.mock.calls[0][1].body)).toMatchObject({ username: 'kent', display_name: 'Kent' });
    expect(JSON.parse(localStorage.getItem(SESSION_KEY)!).token).toBe('reg-token');
    expect(getMediaToken()).toBe('reg-token');
    expect(providerMounts).toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
    expect(getMediaToken()).toBeNull();
    expect(f.mock.calls[1][0]).toBe('/api/auth/logout');
    expect(f.mock.calls[1][1].headers).toEqual({ Authorization: 'Bearer reg-token' });

    await userEvent.type(screen.getByLabelText('Username'), 'kent');
    await userEvent.type(screen.getByLabelText('Password'), 'longenough');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Dashboard for kent')).toBeInTheDocument();
    expect(getMediaToken()).toBe('login-token');
  });

  it('client-side validation is announced and nothing is sent', async () => {
    const f = vi.fn();
    renderRoot(f);
    await userEvent.click(screen.getByRole('button', { name: 'Create an account' }));
    await userEvent.type(screen.getByLabelText('Username'), 'ab');
    await userEvent.type(screen.getByLabelText('Password'), 'short');
    await userEvent.type(screen.getByLabelText('Confirm password'), 'short');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(f).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('Please fix the highlighted fields.');
    const username = screen.getByLabelText('Username');
    expect(username).toHaveAttribute('aria-invalid', 'true');
    expect(username).toHaveAccessibleDescription(/3–32 characters/);
    expect(screen.getByLabelText('Password')).toHaveAccessibleDescription(/at least 8/);
  });

  it('server errors are shown: wrong password, taken username', async () => {
    const f = vi
      .fn()
      .mockResolvedValueOnce(json({ detail: 'Incorrect username or password.' }, 401))
      .mockResolvedValueOnce(json({ detail: { message: 'That username is taken.', field: 'username' } }, 409));
    renderRoot(f);
    await userEvent.type(screen.getByLabelText('Username'), 'kent');
    await userEvent.type(screen.getByLabelText('Password'), 'wrongwrong');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect username or password.');

    await userEvent.click(screen.getByRole('button', { name: 'Create an account' }));
    expect(screen.getByLabelText('Username')).toHaveValue('kent'); // kept when switching
    await userEvent.type(screen.getByLabelText('Password'), 'longenough');
    await userEvent.type(screen.getByLabelText('Confirm password'), 'longenough');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));
    await waitFor(() =>
      expect(screen.getByLabelText('Username')).toHaveAccessibleDescription('That username is taken.')
    );
  });

  it('restores a saved session after checking it with /api/auth/me', async () => {
    saveSession({ token: 'saved', user });
    const f = vi.fn().mockResolvedValue(json({ ...user, display_name: 'Kent H' }));
    renderRoot(f);
    expect(screen.getByText('Checking your session…')).toBeInTheDocument();
    expect(await screen.findByText('Dashboard for kent')).toBeInTheDocument();
    expect(f.mock.calls[0][0]).toBe('/api/auth/me');
    expect(JSON.parse(localStorage.getItem(SESSION_KEY)!).user.display_name).toBe('Kent H');
  });

  it('an expired saved session goes back to sign in with a notice', async () => {
    saveSession({ token: 'old', user });
    renderRoot(vi.fn().mockResolvedValue(json({ detail: 'Your session has expired.' }, 401)));
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.getByText(/session has ended/)).toBeInTheDocument();
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it('any API 401 while using the dashboard returns to sign in', async () => {
    saveSession({ token: 'tok', user });
    const f = vi
      .fn()
      .mockResolvedValueOnce(json(user)) // /me
      .mockResolvedValueOnce(json({ detail: 'Sign in required.' }, 401)); // a later API call
    renderRoot(f);
    await screen.findByText('Dashboard for kent');
    await act(async () => {
      await appProps.authFetch!('/api/alerts');
    });
    expect(f.mock.calls[1][1].headers.get('Authorization')).toBe('Bearer tok');
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.getByText(/session has ended/)).toBeInTheDocument();
  });
});
