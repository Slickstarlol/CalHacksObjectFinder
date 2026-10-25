import { useEffect, useState } from 'react'
import { fetchMe, getJSON, sendJSON, signOut } from './api'
import AskBoard from './AskBoard'
import AuthScreen from './AuthScreen'
import LiveBoard from './LiveBoard'
import PeopleBoard from './PeopleBoard'
import { getSession, setSession, UNAUTHORIZED_EVENT } from './session'
import './App.css'

const ENDED = 'Your session has ended. Please sign in again.'

// Auth gate: until someone is signed in, only the sign-in / create-account
// screen renders and no user data is requested. A 401 from any request
// (expired or revoked session) brings the screen back.
export default function App() {
  const [session, setCurrent] = useState(getSession)
  const [checking, setChecking] = useState(() => getSession() !== null)
  const [notice, setNotice] = useState('')

  useEffect(() => {
    const ended = () => {
      setCurrent(null)
      setNotice(ENDED)
    }
    window.addEventListener(UNAUTHORIZED_EVENT, ended)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, ended)
  }, [])

  // A saved session: confirm it's still valid (and pick up name changes).
  useEffect(() => {
    const saved = getSession()
    if (!saved) return undefined
    let stop = false
    fetchMe()
      .then((user) => {
        if (stop) return
        const next = { ...saved, user }
        setSession(next)
        setCurrent(next)
      })
      .catch(() => {
        // 401 already signed us out; otherwise the server is down: keep the session.
      })
      .finally(() => {
        if (!stop) setChecking(false)
      })
    return () => {
      stop = true
    }
  }, [])

  if (!session || checking) {
    return (
      <AuthScreen
        checking={checking && Boolean(session)}
        notice={notice}
        onSignedIn={(next) => {
          setNotice('')
          setCurrent(next)
        }}
      />
    )
  }

  return (
    <Dashboard
      key={session.user.id}
      user={session.user}
      onSignOut={async () => {
        await signOut()
        setNotice('')
        setCurrent(null)
      }}
    />
  )
}

function Status({ live, serverDown }) {
  if (serverDown) return <p className="status-pill off">Server off</p>
  if (live?.online) return <p className="status-pill live">Camera live</p>
  if (live) return <p className="status-pill off">Camera off</p>
  return <p className="status-pill">Connecting</p>
}

function Dashboard({ user, onSignOut }) {
  const [live, setLive] = useState(null)
  const [belongings, setBelongings] = useState(null)
  const [recent, setRecent] = useState(null)
  const [alerts, setAlerts] = useState([])
  const [serverDown, setServerDown] = useState(false)

  useEffect(() => {
    let stop = false
    async function tick() {
      try {
        const data = await getJSON('/api/live')
        if (stop) return
        setLive(data)
        setServerDown(false)
      } catch {
        if (!stop) setServerDown(true)
      }
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => {
      stop = true
      clearInterval(id)
    }
  }, [])

  useEffect(() => {
    let stop = false
    async function tick() {
      try {
        const [items, sightings, notes] = await Promise.all([
          getJSON('/api/belongings'),
          getJSON('/api/sightings/recent?limit=40'),
          getJSON('/api/alerts'),
        ])
        if (stop) return
        setBelongings(items)
        setRecent(sightings)
        setAlerts(notes)
        setServerDown(false)
      } catch {
        if (!stop) setServerDown(true)
      }
    }
    tick()
    const id = setInterval(tick, 4000)
    return () => {
      stop = true
      clearInterval(id)
    }
  }, [])

  async function dismiss(id) {
    try {
      await sendJSON(`/api/alerts/${id}/dismiss`, undefined, 'POST')
      setAlerts((current) => current.filter((alert) => alert.id !== id))
    } catch {
      // The next refresh will show the alert again if the dismiss did not land.
    }
  }

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <p className="eyebrow">RecallAI</p>
          <h1>Where it went.</h1>
        </div>
        <nav aria-label="Sections">
          <a href="#camera">Camera</a>
          <a href="#places">Last seen</a>
          <a href="#people">People</a>
          <a href="#ask">Ask</a>
        </nav>
        <Status live={live} serverDown={serverDown} />
        <div className="account">
          <span className="who" title={`Signed in as ${user.username}`}>
            <span className="visually-hidden">Signed in as </span>
            {user.display_name || user.username}
          </span>
          <button type="button" className="btn ghost" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </header>

      {serverDown && (
        <p className="banner" role="status">
          The dashboard cannot reach the server. In the project folder, run{' '}
          <code>python -m uvicorn recallai.api.server:app --host 127.0.0.1 --port 8000</code>{' '}(or start it from <code>launcher\RecallAI.bat</code>)
        </p>
      )}

      <main>
        <LiveBoard
          live={live}
          belongings={belongings}
          recent={recent}
          alerts={alerts}
          serverDown={serverDown}
          onDismiss={dismiss}
          onEnrolled={async () => {
            try {
              setBelongings(await getJSON('/api/belongings'))
            } catch {
              // The regular refresh will pick the new item up.
            }
          }}
        />
        <PeopleBoard />
        <AskBoard />
      </main>
    </div>
  )
}
