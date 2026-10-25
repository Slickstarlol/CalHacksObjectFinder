// The signed-in RecallAI session for this dashboard.
//
// API calls send `Authorization: Bearer <token>`. <img> tags can't send
// headers, so snapshot and live-frame URLs get `?token=` (see mediaUrl in
// format.js). The key matches the live dashboard and the server's HTML pages.

export const SESSION_KEY = 'recallai.auth'
export const UNAUTHORIZED_EVENT = 'recallai:unauthorized'

let current = load()

function load() {
  try {
    const saved = JSON.parse(localStorage.getItem(SESSION_KEY) || 'null')
    return saved && typeof saved.token === 'string' && saved.user ? saved : null
  } catch {
    return null
  }
}

export function getSession() {
  return current
}

export function getToken() {
  return current?.token || ''
}

export function setSession(session) {
  current = session
  try {
    if (session) localStorage.setItem(SESSION_KEY, JSON.stringify(session))
    else localStorage.removeItem(SESSION_KEY)
  } catch {
    // Private mode: the session lasts for this tab only.
  }
}

// Called by api.js when the server answers 401: the session expired or was
// revoked. App listens for the event and shows the sign-in screen.
export function signalUnauthorized() {
  if (!current) return
  setSession(null)
  window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
}
