import { useEffect, useId, useRef, useState } from 'react'
import { register, signIn } from './api'
import { validateAccount } from './format'

const EMPTY = { username: '', displayName: '', password: '', confirm: '' }

// Sign in / create account. Nothing else of the dashboard renders until this
// succeeds; App shows it again on sign-out or when a request returns 401.
export default function AuthScreen({ notice, checking, onSignedIn }) {
  const [mode, setMode] = useState('login')
  const [form, setForm] = useState(EMPTY)
  const [errors, setErrors] = useState({})
  const [busy, setBusy] = useState(false)
  const id = useId()
  const first = useRef(null)
  const creating = mode === 'register'

  useEffect(() => {
    first.current?.focus()
  }, [mode, checking])

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
    setErrors((current) => {
      if (!current[field] && !current.form) return current
      const next = { ...current }
      delete next[field]
      delete next.form
      return next
    })
  }

  function switchMode() {
    setMode((current) => (current === 'login' ? 'register' : 'login'))
    setErrors({})
    setForm((current) => ({ ...EMPTY, username: current.username }))
  }

  async function onSubmit(event) {
    event.preventDefault()
    const found = validateAccount({ mode, ...form })
    setErrors(found)
    if (Object.keys(found).length) return
    setBusy(true)
    try {
      const session = creating ? await register(form) : await signIn(form.username, form.password)
      onSignedIn(session)
    } catch (err) {
      const field = err.field === 'display_name' ? 'displayName' : err.field
      setErrors(field && ['username', 'password', 'displayName'].includes(field)
        ? { [field]: err.message }
        : { form: err.message })
    } finally {
      setBusy(false)
    }
  }

  const describe = (field, hint) => (errors[field]
    ? `${id}-${field}-error`
    : hint ? `${id}-${field}-hint` : undefined)

  const fieldError = (field) => errors[field] && (
    <span className="field-error" id={`${id}-${field}-error`}>{errors[field]}</span>
  )

  return (
    <div className="auth-shell">
      <main className="auth-card" aria-labelledby={`${id}-title`}>
        <p className="eyebrow">RecallAI</p>
        <h1 id={`${id}-title`}>{creating ? 'Make your account.' : 'Welcome back.'}</h1>
        <p className="lede">
          {creating
            ? 'Your belongings, sightings, alerts and people stay private to your account. The first account on this computer also takes over everything recorded before accounts existed.'
            : 'Sign in to see where your things were last seen.'}
        </p>

        {notice && !errors.form && <p className="banner" role="status">{notice}</p>}

        {checking ? (
          <p className="muted" role="status">Checking your session…</p>
        ) : (
          <form onSubmit={onSubmit} noValidate aria-busy={busy}>
            <div className="field">
              <label htmlFor={`${id}-username`}>Username</label>
              <input
                ref={first}
                id={`${id}-username`}
                value={form.username}
                onChange={(event) => update('username', event.target.value)}
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                required
                aria-invalid={errors.username ? true : undefined}
                aria-describedby={describe('username', creating)}
              />
              {creating && !errors.username && (
                <span className="hint" id={`${id}-username-hint`}>
                  3–32 characters: a–z, 0–9, dot, dash, underscore.
                </span>
              )}
              {fieldError('username')}
            </div>

            {creating && (
              <div className="field">
                <label htmlFor={`${id}-displayName`}>Display name <span className="hint">(optional)</span></label>
                <input
                  id={`${id}-displayName`}
                  value={form.displayName}
                  onChange={(event) => update('displayName', event.target.value)}
                  autoComplete="name"
                  maxLength={64}
                  aria-invalid={errors.displayName ? true : undefined}
                  aria-describedby={describe('displayName', false)}
                />
                {fieldError('displayName')}
              </div>
            )}

            <div className="field">
              <label htmlFor={`${id}-password`}>Password</label>
              <input
                id={`${id}-password`}
                type="password"
                value={form.password}
                onChange={(event) => update('password', event.target.value)}
                autoComplete={creating ? 'new-password' : 'current-password'}
                required
                aria-invalid={errors.password ? true : undefined}
                aria-describedby={describe('password', creating)}
              />
              {creating && !errors.password && (
                <span className="hint" id={`${id}-password-hint`}>At least 8 characters.</span>
              )}
              {fieldError('password')}
            </div>

            {creating && (
              <div className="field">
                <label htmlFor={`${id}-confirm`}>Confirm password</label>
                <input
                  id={`${id}-confirm`}
                  type="password"
                  value={form.confirm}
                  onChange={(event) => update('confirm', event.target.value)}
                  autoComplete="new-password"
                  required
                  aria-invalid={errors.confirm ? true : undefined}
                  aria-describedby={describe('confirm', false)}
                />
                {fieldError('confirm')}
              </div>
            )}

            <p className="form-error" role="alert" aria-live="assertive">
              {errors.form || (Object.keys(errors).length ? 'Please fix the highlighted fields.' : '')}
            </p>

            <div className="actions">
              <button type="submit" className="btn auth-submit" disabled={busy}>
                {busy
                  ? (creating ? 'Creating account…' : 'Signing in…')
                  : (creating ? 'Create account' : 'Sign in')}
              </button>
            </div>
          </form>
        )}

        <p className="auth-switch">
          {creating ? 'Already have an account?' : 'New here?'}{' '}
          <button type="button" className="link-btn" onClick={switchMode}>
            {creating ? 'Sign in' : 'Create an account'}
          </button>
        </p>
      </main>
    </div>
  )
}
