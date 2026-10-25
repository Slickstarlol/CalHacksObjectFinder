// The server names a belonging after its YOLO category ("cup", "cell phone").
// The name and emoji picked during onboarding live here, keyed by belonging id,
// so the dashboard can show "Lucky mug" without a backend change.
const KEY = 'recallai.nicknames'

export function loadNicknames() {
  try {
    return JSON.parse(localStorage.getItem(KEY)) || {}
  } catch {
    return {}
  }
}

export function saveNickname(id, nickname) {
  const all = { ...loadNicknames(), [id]: nickname }
  try {
    localStorage.setItem(KEY, JSON.stringify(all))
  } catch {
    // Private windows can refuse storage. The server name still shows.
  }
  return all
}

export function displayName(nicknames, id, fallback) {
  const nick = id ? nicknames[id] : null
  if (!nick?.name) return fallback
  return nick.emoji ? `${nick.emoji} ${nick.name}` : nick.name
}
