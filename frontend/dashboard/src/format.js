import { getToken } from './session'

// Snapshot / live-frame URL for an <img>. The server only serves a snapshot
// to the account that owns it, and <img> can't send headers, so the session
// token rides along as ?token=.
export function mediaUrl(path) {
  if (!path) return ''
  const url = path.startsWith('/') ? path : `/${path}`
  const token = getToken()
  if (!token || !/^\/(snapshots|api\/live)\//.test(url)) return url
  return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`
}

export const USERNAME_PATTERN = /^[a-z0-9_.-]{3,32}$/

// Same rules as the server (backend/recallai/memory/users.py).
export function validateAccount({ mode, username, displayName = '', password, confirm = '' }) {
  const errors = {}
  const name = username.trim().toLowerCase()
  if (mode === 'register') {
    if (name.length < 3 || name.length > 32) errors.username = 'Username must be 3–32 characters.'
    else if (!USERNAME_PATTERN.test(name)) {
      errors.username = 'Use only lowercase letters, numbers, dots, dashes and underscores.'
    }
    if (displayName.trim().length > 64) errors.displayName = 'Display name must be at most 64 characters.'
    if (password.length < 8) errors.password = 'Password must be at least 8 characters.'
    else if (confirm !== password) errors.confirm = 'Passwords do not match.'
  } else {
    if (!name) errors.username = 'Enter your username.'
    if (!password) errors.password = 'Enter your password.'
  }
  return errors
}

export function ago(iso) {
  const then = new Date(iso)
  const seconds = (Date.now() - then.getTime()) / 1000
  if (Number.isNaN(seconds)) return ''
  if (seconds < 45) return 'just now'
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`
  return `${Math.round(seconds / 86400)} d ago`
}

export function when(iso) {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function todayLocal() {
  const date = new Date()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

export function eventLabel(event) {
  if (event === 'left_view') return 'Last place'
  return 'Seen'
}

export function groupDetections(detections = []) {
  const grouped = new Map()
  for (const detection of detections) {
    const name = detection.category || 'object'
    const row = grouped.get(name) || { category: name, count: 0, confidence: 0 }
    row.count += 1
    row.confidence = Math.max(row.confidence, Number(detection.confidence) || 0)
    grouped.set(name, row)
  }
  return [...grouped.values()].sort((a, b) => b.confidence - a.confidence)
}

export function errorMessage(data, fallback = 'Request failed') {
  const detail = data?.detail
  if (typeof detail === 'string') return detail
  if (typeof detail?.message === 'string') return detail.message
  if (Array.isArray(detail)) {
    const text = detail.map((item) => item?.msg || String(item)).join(' ')
    if (text) return text
  }
  return fallback
}
