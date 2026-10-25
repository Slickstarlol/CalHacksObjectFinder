import { errorMessage } from './format'
import { getToken, setSession, signalUnauthorized } from './session'

async function request(url, options = {}, { auth = true } = {}) {
  const headers = { ...(options.headers || {}) }
  const token = getToken()
  if (auth && token) headers.Authorization = `Bearer ${token}`
  let response
  try {
    response = await fetch(url, { ...options, headers })
  } catch {
    throw new Error('Cannot reach the RecallAI server on port 8000')
  }
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 401 && auth) signalUnauthorized()
    const error = new Error(errorMessage(data, `Request failed (${response.status})`))
    error.status = response.status
    error.field = typeof data?.detail?.field === 'string' ? data.detail.field : undefined
    throw error
  }
  return data
}

export function getJSON(url) {
  return request(url)
}

export function sendJSON(url, body, method = 'POST') {
  const options = { method, headers: {} }
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json'
    options.body = JSON.stringify(body)
  }
  return request(url, options)
}

// ---- accounts -------------------------------------------------------------
function post(url, body) {
  return request(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }, { auth: false })
}

function remember(data) {
  const session = { token: data.token, user: data.user, expires_at: data.expires_at }
  setSession(session)
  return session
}

export async function signIn(username, password) {
  return remember(await post('/api/auth/login', { username: username.trim().toLowerCase(), password }))
}

export async function register({ username, displayName, password }) {
  return remember(await post('/api/auth/register', {
    username: username.trim().toLowerCase(),
    display_name: displayName.trim(),
    password,
  }))
}

export async function fetchMe() {
  return request('/api/auth/me')
}

export async function signOut() {
  const token = getToken()
  setSession(null)
  if (!token) return
  try {
    await fetch('/api/auth/logout', { method: 'POST', headers: { Authorization: `Bearer ${token}` } })
  } catch {
    // Signed out locally either way.
  }
}
