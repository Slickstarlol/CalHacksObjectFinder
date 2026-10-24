import { useEffect, useState } from 'react'
import { getJSON, sendJSON } from './api'
import AskBoard from './AskBoard'
import LiveBoard from './LiveBoard'
import PeopleBoard from './PeopleBoard'
import './App.css'

function Status({ live, serverDown }) {
  if (serverDown) return <p className="status-pill off">Server off</p>
  if (live?.online) return <p className="status-pill live">Camera live</p>
  if (live) return <p className="status-pill off">Camera off</p>
  return <p className="status-pill">Connecting</p>
}

export default function App() {
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
      </header>

      {serverDown && (
        <p className="banner" role="status">
          The dashboard cannot reach the server. In the project folder, run{' '}
          <code>uvicorn server:app --host 127.0.0.1 --port 8000</code>
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
        />
        <PeopleBoard />
        <AskBoard />
      </main>
    </div>
  )
}
