import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import {
  AuthError,
  normalizeUsername,
  validateLogin,
  validateRegistration,
  type FormErrors,
  type RegisterForm,
} from '../lib/auth';

type Mode = 'signin' | 'register';

type Props = {
  onSignIn: (username: string, password: string) => Promise<void>;
  onSignUp: (form: RegisterForm) => Promise<void>;
  /** Shown above the form, e.g. "Your session has ended." */
  notice?: string | null;
  /** True while a saved session is being checked. */
  checking?: boolean;
  initialMode?: Mode;
};

const EMPTY: RegisterForm = { username: '', displayName: '', password: '', confirm: '' };

/** Sign-in / create-account screen. The dashboard is not rendered until this succeeds. */
export function AuthScreen({ onSignIn, onSignUp, notice, checking, initialMode = 'signin' }: Props) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [form, setForm] = useState<RegisterForm>(EMPTY);
  const [errors, setErrors] = useState<FormErrors>({});
  const [busy, setBusy] = useState(false);
  const id = useId();
  const firstField = useRef<HTMLInputElement>(null);
  const register = mode === 'register';

  useEffect(() => {
    firstField.current?.focus();
  }, [mode]);

  const set = (key: keyof RegisterForm) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setForm(f => ({ ...f, [key]: e.target.value }));
    if (errors[key] || errors.form) setErrors(({ [key]: _gone, form: _f, ...rest }) => rest);
  };

  const switchMode = () => {
    setMode(m => (m === 'signin' ? 'register' : 'signin'));
    setErrors({});
    setForm(f => ({ ...EMPTY, username: f.username }));
  };

  async function submit(e: FormEvent) {
    e.preventDefault();
    const found = register ? validateRegistration(form) : validateLogin(form.username, form.password);
    setErrors(found);
    if (Object.keys(found).length) return;
    setBusy(true);
    try {
      if (register) await onSignUp(form);
      else await onSignIn(normalizeUsername(form.username), form.password);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Something went wrong. Try again.';
      const field = err instanceof AuthError ? err.field : undefined;
      setErrors(
        field === 'username' || field === 'password' || field === 'display_name'
          ? { [field === 'display_name' ? 'displayName' : field]: message }
          : { form: message }
      );
    } finally {
      setBusy(false);
    }
  }

  const hasHint = (key: keyof FormErrors) => register && (key === 'username' || key === 'password');
  const fieldProps = (key: keyof FormErrors) => ({
    id: `${id}-${key}`,
    'aria-invalid': errors[key] ? true : undefined,
    'aria-describedby': errors[key]
      ? `${id}-${key}-error`
      : hasHint(key)
        ? `${id}-${key}-hint`
        : undefined,
  });
  const fieldError = (key: keyof FormErrors) =>
    errors[key] ? (
      <p className="auth-field-error" id={`${id}-${key}-error`}>
        {errors[key]}
      </p>
    ) : null;

  return (
    <div className="auth-page">
      <main className="auth-card" aria-labelledby={`${id}-title`}>
        <div className="brand auth-brand">
          <span className="brand-mark" aria-hidden="true" />
          <span className="auth-brand-name">RecallAI</span>
        </div>
        <h1 id={`${id}-title`}>{register ? 'Create your account' : 'Sign in'}</h1>
        <p className="muted auth-lede">
          {register
            ? 'Your belongings, sightings, alerts and people are private to your account. The first account on this computer also takes over anything recorded before accounts existed.'
            : 'Sign in to see where your belongings were last seen.'}
        </p>
        {notice && !errors.form && (
          <p className="notice auth-notice" role="status">
            {notice}
          </p>
        )}
        {checking ? (
          <p className="muted" role="status">
            Checking your session…
          </p>
        ) : (
          <form className="auth-form" onSubmit={submit} noValidate aria-busy={busy}>
            <div className="auth-field">
              <label htmlFor={`${id}-username`}>Username</label>
              <input
                ref={firstField}
                {...fieldProps('username')}
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                value={form.username}
                onChange={set('username')}
                required
              />
              {register && !errors.username && (
                <p className="auth-hint" id={`${id}-username-hint`}>
                  3–32 characters: a–z, 0–9, dot, dash, underscore.
                </p>
              )}
              {fieldError('username')}
            </div>
            {register && (
              <div className="auth-field">
                <label htmlFor={`${id}-displayName`}>
                  Display name <span className="muted">(optional)</span>
                </label>
                <input
                  {...fieldProps('displayName')}
                  name="display_name"
                  autoComplete="name"
                  maxLength={64}
                  value={form.displayName}
                  onChange={set('displayName')}
                />
                {fieldError('displayName')}
              </div>
            )}
            <div className="auth-field">
              <label htmlFor={`${id}-password`}>Password</label>
              <input
                {...fieldProps('password')}
                type="password"
                name="password"
                autoComplete={register ? 'new-password' : 'current-password'}
                value={form.password}
                onChange={set('password')}
                required
              />
              {register && !errors.password && (
                <p className="auth-hint" id={`${id}-password-hint`}>
                  At least 8 characters.
                </p>
              )}
              {fieldError('password')}
            </div>
            {register && (
              <div className="auth-field">
                <label htmlFor={`${id}-confirm`}>Confirm password</label>
                <input
                  {...fieldProps('confirm')}
                  type="password"
                  name="confirm"
                  autoComplete="new-password"
                  value={form.confirm}
                  onChange={set('confirm')}
                  required
                />
                {fieldError('confirm')}
              </div>
            )}
            <div role="alert" aria-live="assertive" className="auth-form-error">
              {errors.form ?? (Object.keys(errors).length ? 'Please fix the highlighted fields.' : '')}
            </div>
            <button type="submit" className="btn btn-primary auth-submit" disabled={busy}>
              {busy ? (register ? 'Creating account…' : 'Signing in…') : register ? 'Create account' : 'Sign in'}
            </button>
          </form>
        )}
        <p className="auth-switch">
          {register ? 'Already have an account?' : 'New to RecallAI?'}{' '}
          <button type="button" className="btn-link auth-switch-btn" onClick={switchMode}>
            {register ? 'Sign in' : 'Create an account'}
          </button>
        </p>
      </main>
    </div>
  );
}
