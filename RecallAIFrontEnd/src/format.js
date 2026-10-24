export function mediaUrl(path) {
  if (!path) return ''
  return path.startsWith('/') ? path : `/${path}`
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
  if (Array.isArray(detail)) {
    const text = detail.map((item) => item?.msg || String(item)).join(' ')
    if (text) return text
  }
  return fallback
}
