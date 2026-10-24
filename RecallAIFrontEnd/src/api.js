import { errorMessage } from './format'

async function request(url, options) {
  let response
  try {
    response = await fetch(url, options)
  } catch {
    throw new Error('Cannot reach the RecallAI server on port 8000')
  }
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(errorMessage(data, `Request failed (${response.status})`))
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
