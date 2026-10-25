import { useState } from 'react'
import Shot from './Shot'
import { ago } from './format'
import { sendJSON } from './api'

const EXAMPLES = [
  'Where did I leave my bag?',
  'What did I have with me this morning?',
  'Who have I saved, and how do I know them?',
]

function Answer({ text }) {
  const parts = String(text || '').split(/(\[#\d+\])/g)
  return parts.map((part, index) => (
    /^\[#\d+\]$/.test(part)
      ? <span key={index} className="cite">{part}</span>
      : <span key={index}>{part}</span>
  ))
}

export default function AskBoard() {
  const [question, setQuestion] = useState('')
  const [history, setHistory] = useState([])
  const [turns, setTurns] = useState([])
  const [busy, setBusy] = useState(false)

  async function ask(text) {
    const trimmed = text.trim()
    if (!trimmed || busy) return
    setQuestion('')
    setBusy(true)
    const pending = {
      id: Date.now(),
      question: trimmed,
      pending: true,
      answer: '',
      sightings: [],
      steps: [],
      error: '',
    }
    setTurns((current) => [pending, ...current])
    try {
      const data = await sendJSON('/api/ask', { question: trimmed, history })
      setHistory((current) => [
        ...current,
        { role: 'user', content: trimmed },
        { role: 'assistant', content: data.answer || '' },
      ].slice(-10))
      setTurns((current) => current.map((turn) => (
        turn.id === pending.id
          ? {
            ...turn,
            pending: false,
            answer: data.answer || '',
            sightings: data.sightings || [],
            steps: data.steps || [],
          }
          : turn
      )))
    } catch (err) {
      setTurns((current) => current.map((turn) => (
        turn.id === pending.id
          ? { ...turn, pending: false, error: err.message }
          : turn
      )))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section id="ask" className="section">
      <div className="section-head">
        <p className="kicker">04 — Ask</p>
        <h2>Ask RecallAI</h2>
        <p className="lede">
          Questions about belongings go through the memory agent. Questions about
          people use the list you saved above.
        </p>
      </div>

      <form
        className="ask-bar"
        onSubmit={(event) => {
          event.preventDefault()
          ask(question)
        }}
      >
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Where did I leave my bag?"
          maxLength={1000}
          aria-label="Question for RecallAI"
        />
        <button type="submit" className="btn" disabled={busy || !question.trim()}>
          {busy ? 'Looking…' : 'Ask'}
        </button>
      </form>
      <div className="examples">
        {EXAMPLES.map((example) => (
          <button key={example} type="button" onClick={() => ask(example)} disabled={busy}>
            {example}
          </button>
        ))}
      </div>

      <div className="chat">
        {turns.map((turn) => (
          <article key={turn.id} className="turn">
            <p className="q">{turn.question}</p>
            {turn.pending ? (
              <p className="muted">Looking through your memories…</p>
            ) : turn.error ? (
              <p className="form-error" role="alert">{turn.error}</p>
            ) : (
              <p className="a"><Answer text={turn.answer} /></p>
            )}
            {turn.sightings.length > 0 && (
              <div className="refs">
                {turn.sightings.map((sighting) => (
                  <figure key={sighting.id}>
                    <Shot
                      className="ref-shot"
                      path={sighting.crop_path || sighting.image_path}
                      alt=""
                    />
                    <figcaption>#{sighting.id} · {ago(sighting.timestamp)}</figcaption>
                  </figure>
                ))}
              </div>
            )}
            {turn.steps.length > 0 && (
              <details>
                <summary>How this was found ({turn.steps.length} steps)</summary>
                <ul>
                  {turn.steps.map((step, index) => (
                    <li key={`${turn.id}-${index}`}>
                      {step.tool} {JSON.stringify(step.input)}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </article>
        ))}
      </div>
    </section>
  )
}
