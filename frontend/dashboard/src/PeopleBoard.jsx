import { useEffect, useState } from 'react'
import { getJSON, sendJSON } from './api'
import { todayLocal } from './format'

const EMPTY = {
  name: '',
  description: '',
  relationship: '',
  known_since: '',
}

const RELATIONSHIPS = [
  'Friend',
  'Family',
  'Partner',
  'Coworker',
  'Classmate',
  'Roommate',
  'Neighbor',
  'Mentor',
  'Acquaintance',
]

export default function PeopleBoard() {
  const [people, setPeople] = useState(null)
  const [form, setForm] = useState(EMPTY)
  const [editingId, setEditingId] = useState(null)
  const [error, setError] = useState('')
  const [loadError, setLoadError] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmId, setConfirmId] = useState(null)

  async function load() {
    const rows = await getJSON('/api/people')
    setPeople(rows)
    setLoadError('')
  }

  useEffect(() => {
    let stop = false
    getJSON('/api/people')
      .then((rows) => {
        if (!stop) setPeople(rows)
      })
      .catch((err) => {
        if (!stop) setLoadError(err.message)
      })
    return () => {
      stop = true
    }
  }, [])

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  function startEdit(person) {
    setEditingId(person.id)
    setConfirmId(null)
    setError('')
    setForm({
      name: person.name,
      description: person.description || '',
      relationship: person.relationship || '',
      known_since: person.known_since || '',
    })
  }

  function cancelEdit() {
    setEditingId(null)
    setForm(EMPTY)
    setError('')
  }

  async function onSubmit(event) {
    event.preventDefault()
    setError('')
    setBusy(true)
    const payload = {
      name: form.name,
      description: form.description,
      relationship: form.relationship,
      known_since: form.known_since || null,
    }
    try {
      if (editingId) {
        await sendJSON(`/api/people/${editingId}`, payload, 'PUT')
      } else {
        await sendJSON('/api/people', payload)
      }
      setForm(EMPTY)
      setEditingId(null)
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function remove(id) {
    setError('')
    setBusy(true)
    try {
      await sendJSON(`/api/people/${id}`, undefined, 'DELETE')
      if (editingId === id) cancelEdit()
      setConfirmId(null)
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section id="people" className="section">
      <div className="section-head">
        <p className="kicker">03 — People</p>
        <h2>Who you know</h2>
        <p className="lede">
          Save a person with a description, how they relate to you, and the date
          you met. RecallAI keeps the “how long” current from that date.
        </p>
      </div>

      <div className="people-layout">
        <div className="directory">
          {people === null ? (
            <p className={loadError ? 'form-error' : 'muted'}>{loadError || 'Loading people…'}</p>
          ) : people.length === 0 ? (
            <p className="empty">No one saved yet. Add someone with the form.</p>
          ) : (
            <ul className="person-list">
              {people.map((person) => (
                <li key={person.id} className="person">
                  <div className="person-top">
                    <b>{person.name}</b>
                    {person.relationship && (
                      <span className="badge">{person.relationship}</span>
                    )}
                  </div>
                  <p className={person.description ? 'desc' : 'muted'}>
                    {person.description || 'No description yet.'}
                  </p>
                  <p className="known">
                    {person.known_for
                      ? `Known for ${person.known_for}`
                      : 'How long you have known them is not set'}
                    {person.known_since ? ` · since ${person.known_since}` : ''}
                  </p>
                  <div className="actions">
                    <button type="button" className="btn ghost" onClick={() => startEdit(person)}>
                      Edit
                    </button>
                    {confirmId === person.id ? (
                      <>
                        <button
                          type="button"
                          className="btn danger"
                          disabled={busy}
                          onClick={() => remove(person.id)}
                        >
                          Remove
                        </button>
                        <button type="button" className="btn ghost" onClick={() => setConfirmId(null)}>
                          Keep
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        className="btn ghost"
                        onClick={() => setConfirmId(person.id)}
                      >
                        Remove
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <form className="person-form" onSubmit={onSubmit}>
          <h3>{editingId ? 'Edit person' : 'Add a person'}</h3>
          <label className="field">
            Name
            <input
              value={form.name}
              onChange={(event) => update('name', event.target.value)}
              maxLength={80}
              required
              autoComplete="name"
            />
          </label>
          <label className="field">
            Description
            <textarea
              value={form.description}
              onChange={(event) => update('description', event.target.value)}
              maxLength={2000}
              rows={4}
              placeholder="How you’d recognize them, and anything worth remembering."
            />
          </label>
          <label className="field">
            Relationship to you
            <input
              value={form.relationship}
              onChange={(event) => update('relationship', event.target.value)}
              maxLength={80}
              list="relationships"
              placeholder="Friend, sister, lab partner…"
            />
            <datalist id="relationships">
              {RELATIONSHIPS.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </label>
          <label className="field">
            Known since
            <input
              type="date"
              value={form.known_since}
              max={todayLocal()}
              onChange={(event) => update('known_since', event.target.value)}
            />
            <span className="hint">Used to show how long you have known them.</span>
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="actions">
            <button type="submit" className="btn" disabled={busy}>
              {editingId ? 'Save changes' : 'Add person'}
            </button>
            {editingId && (
              <button type="button" className="btn ghost" onClick={cancelEdit}>
                Cancel
              </button>
            )}
          </div>
        </form>
      </div>
    </section>
  )
}
