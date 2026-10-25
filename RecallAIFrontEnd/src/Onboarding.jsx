import { useEffect, useRef, useState } from 'react'
import { sendJSON } from './api'
import { saveNickname } from './nicknames'
import './Onboarding.css'

const SUGGESTIONS = [
  { emoji: '🔑', name: 'Keys' },
  { emoji: '👛', name: 'Wallet' },
  { emoji: '🎒', name: 'Backpack' },
  { emoji: '💻', name: 'Laptop' },
  { emoji: '🎧', name: 'Headphones' },
  { emoji: '📱', name: 'Phone' },
  { emoji: '🕶️', name: 'Glasses' },
  { emoji: '🥤', name: 'Bottle' },
]

const EMOJIS = [
  '📦', '🔑', '👛', '🎒', '💻', '🎧', '📱', '🕶️',
  '🥤', '☕', '⌚', '📷', '🎮', '📚', '🖱️', '🧸',
  '💍', '🧢', '👟', '🌂', '💊', '🪴', '🎸', '⚽',
]

const TICKS = 72
const RING = 132 // inner radius of the tick ring, in viewBox units
// A done report that lands right after start is a stale poll from the last
// session. Five angles take far longer than this to collect.
const MIN_SCAN_MS = 1500

function TickRing({ progress, pending }) {
  const lit = Math.round(progress * TICKS)
  const glow = Math.round(Math.min(1, progress + pending) * TICKS)
  return (
    <svg className="ob-ring" viewBox="-160 -160 320 320" aria-hidden="true">
      {Array.from({ length: TICKS }, (_, i) => {
        const state = i < lit ? 'on' : i < glow ? 'next' : ''
        return (
          <g key={i} transform={`rotate(${(i * 360) / TICKS})`}>
            <line
              className={`ob-tick ${state}`}
              style={{ transitionDelay: state === 'on' ? `${(i % (TICKS / 5)) * 12}ms` : '0ms' }}
              x1="0"
              y1={-RING}
              x2="0"
              y2={-RING - 12}
            />
          </g>
        )
      })}
    </svg>
  )
}

function scanCopy(enroll, online) {
  if (!online) {
    return { title: 'Camera is off', body: 'Start the vision loop to keep going.' }
  }
  const saved = enroll?.saved || 0
  const needed = enroll?.needed || 5
  const thing = enroll?.category ? `the ${enroll.category}` : 'it'
  switch (enroll?.phase) {
    case 'turn':
      return { title: 'Now slowly turn it', body: `Show the camera a new side of ${thing}.` }
    case 'retry':
      return { title: 'Let’s try that again', body: enroll.message }
    case 'hold':
      if (!enroll.category && saved === 0) {
        return { title: 'Show it to the camera', body: 'Hold one object in the circle and keep it still.' }
      }
      return { title: 'Hold still', body: `Capturing angle ${Math.min(saved + 1, needed)} of ${needed}.` }
    default:
      return { title: 'Getting ready', body: 'Waking up the camera…' }
  }
}

export default function Onboarding({ enroll, setEnroll, frameUrl, online, onClose }) {
  const [step, setStep] = useState('name')
  const [name, setName] = useState('')
  const [emoji, setEmoji] = useState('📦')
  const [picking, setPicking] = useState(false)
  const [error, setError] = useState('')
  const [photo, setPhoto] = useState('')
  const [leaving, setLeaving] = useState(false)
  const [armed, setArmed] = useState(false)
  const nameRef = useRef(null)

  const trimmed = name.trim()
  const saved = enroll?.saved || 0
  const needed = enroll?.needed || 5
  const scanning = step === 'scan' && armed

  async function start() {
    setError('')
    setStep('scan')
    setArmed(false)
    try {
      const data = await sendJSON('/api/enroll/start')
      setEnroll(data)
      setTimeout(() => setArmed(true), MIN_SCAN_MS)
    } catch (err) {
      setError(err.message === 'Not Found'
        ? 'Add is not on this server yet. Restart the server, then try again.'
        : err.message)
    }
  }

  function close() {
    setLeaving(true)
    setTimeout(onClose, 280)
  }

  async function cancel() {
    if (step === 'scan' && enroll?.active) {
      try {
        setEnroll(await sendJSON('/api/enroll/cancel'))
      } catch {
        // The angles already saved stay on disk either way.
      }
    }
    close()
  }

  if (scanning && enroll?.phase === 'done' && !enroll.active) {
    setPhoto(frameUrl)
    setStep('done')
  }

  const doneId = step === 'done' ? enroll?.belonging_id : null
  useEffect(() => {
    if (doneId) saveNickname(doneId, { name: trimmed, emoji })
  }, [doneId, trimmed, emoji])

  useEffect(() => {
    function onKey(event) {
      if (event.key === 'Escape') {
        if (picking) setPicking(false)
        else cancel()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const progress = step === 'done' ? 1 : saved / needed
  const holding = enroll?.phase === 'hold' && (enroll.category || saved > 0)
  const copy = scanCopy(enroll, online)
  const stepIndex = { name: 0, scan: 1, done: 2 }[step]

  return (
    <div className={leaving ? 'ob-scrim leaving' : 'ob-scrim'} onMouseDown={(e) => e.target === e.currentTarget && cancel()}>
      <div className="ob-sheet" role="dialog" aria-modal="true" aria-labelledby="ob-title">
        <div className="ob-top">
          <div className="ob-steps" aria-label={`Step ${stepIndex + 1} of 3`}>
            {[0, 1, 2].map((i) => (
              <i key={i} className={i < stepIndex ? 'done' : i === stepIndex ? 'on' : ''} />
            ))}
          </div>
          {step !== 'done' && (
            <button type="button" className="ob-x" aria-label="Cancel" onClick={cancel}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
            </button>
          )}
        </div>

        {step === 'name' && (
          <form
            className="ob-pane"
            key="name"
            onSubmit={(e) => {
              e.preventDefault()
              if (trimmed) start()
            }}
          >
            <div className="ob-emoji-wrap">
              <button
                type="button"
                className="ob-emoji"
                aria-label={`Icon ${emoji}. Change icon`}
                aria-expanded={picking}
                onClick={() => setPicking((v) => !v)}
              >
                {emoji}
                <span className="ob-emoji-edit" aria-hidden="true">
                  <svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16v4z" /></svg>
                </span>
              </button>
              {picking && (
                <div className="ob-emoji-grid" role="listbox" aria-label="Choose an icon">
                  {EMOJIS.map((e) => (
                    <button
                      key={e}
                      type="button"
                      role="option"
                      aria-selected={e === emoji}
                      className={e === emoji ? 'on' : ''}
                      onClick={() => {
                        setEmoji(e)
                        setPicking(false)
                        nameRef.current?.focus()
                      }}
                    >
                      {e}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <h2 id="ob-title" className="ob-title">What do you want to call it?</h2>
            <p className="ob-body">This is the name you’ll see when RecallAI spots it.</p>

            <input
              ref={nameRef}
              className="ob-input"
              value={name}
              maxLength={32}
              placeholder="e.g. Lucky mug"
              aria-label="Name"
              autoFocus
              onChange={(e) => setName(e.target.value)}
            />

            <div className="ob-chips" aria-label="Suggestions">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s.name}
                  type="button"
                  className={trimmed === s.name ? 'ob-chip on' : 'ob-chip'}
                  onClick={() => {
                    setName(s.name)
                    setEmoji(s.emoji)
                  }}
                >
                  <span aria-hidden="true">{s.emoji}</span> {s.name}
                </button>
              ))}
            </div>

            <button type="submit" className="ob-primary" disabled={!trimmed || !online}>
              {online ? 'Continue' : 'Camera is off'}
            </button>
          </form>
        )}

        {step === 'scan' && (
          <div className="ob-pane" key="scan">
            <div className={holding ? 'ob-viewer holding' : 'ob-viewer'}>
              <TickRing progress={progress} pending={holding ? 1 / needed : 0} />
              <div className="ob-lens">
                {online && frameUrl ? <img src={frameUrl} alt="Live camera" /> : <span>{emoji}</span>}
              </div>
              {enroll?.category && (
                <span className="ob-detected">
                  <i aria-hidden="true" /> {enroll.category}
                </span>
              )}
            </div>

            <div className="ob-scan-copy" aria-live="polite">
              <h2 id="ob-title" className="ob-title">{error ? 'Couldn’t start' : copy.title}</h2>
              <p className="ob-body">{error || copy.body}</p>
            </div>

            <div className="ob-angles" aria-label={`${saved} of ${needed} angles captured`}>
              {Array.from({ length: needed }, (_, i) => (
                <span key={i} className={i < saved ? 'on' : i === saved && holding ? 'next' : ''}>
                  <b />
                </span>
              ))}
            </div>
            <p className="ob-meta">{saved} of {needed} angles · {emoji} {trimmed}</p>

            {error ? (
              <button type="button" className="ob-primary" onClick={start}>Try again</button>
            ) : (
              <button type="button" className="ob-secondary" onClick={cancel}>Cancel</button>
            )}
          </div>
        )}

        {step === 'done' && (
          <div className="ob-pane ob-done" key="done">
            <div className="ob-badge">
              <div className="ob-photo">
                {photo ? <img src={photo} alt="" /> : <span>{emoji}</span>}
              </div>
              <svg className="ob-check" viewBox="0 0 52 52" aria-hidden="true">
                <circle cx="26" cy="26" r="24" />
                <path d="M15 27l7 7 15-16" />
              </svg>
            </div>
            <h2 id="ob-title" className="ob-title">You’re all good!</h2>
            <p className="ob-body ob-done-line">
              <b>{emoji} {trimmed}</b> is being tracked.
            </p>
            <p className="ob-body small">
              RecallAI will remember where it was last seen and let you know if it gets left behind.
            </p>
            <button type="button" className="ob-primary" autoFocus onClick={close}>Done</button>
          </div>
        )}
      </div>
    </div>
  )
}
