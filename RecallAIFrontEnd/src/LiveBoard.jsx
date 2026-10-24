import { useEffect, useState } from 'react'
import Shot from './Shot'
import { ago, eventLabel, groupDetections, mediaUrl, when } from './format'
import { getJSON } from './api'

function scorePercent(score) {
  if (score == null || Number.isNaN(Number(score))) return null
  return Math.round(Number(score) * 100)
}

export default function LiveBoard({
  live,
  belongings,
  recent,
  alerts,
  serverDown,
  onDismiss,
}) {
  const online = !serverDown && live?.online === true
  const [frameUrl, setFrameUrl] = useState('')
  const [frameWhileOnline, setFrameWhileOnline] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  const [trailState, setTrailState] = useState({ id: null, rows: null })

  if (online !== frameWhileOnline) {
    setFrameWhileOnline(online)
    if (!online) setFrameUrl('')
  }

  const shownFrame = online ? frameUrl : ''
  const trail = selectedId && trailState.id === selectedId ? trailState.rows : null

  useEffect(() => {
    if (!online) return undefined
    let stop = false
    let timer
    const tick = () => {
      const img = new Image()
      const url = `/api/live/frame.jpg?t=${Date.now()}`
      img.onload = () => {
        if (stop) return
        setFrameUrl(url)
        timer = setTimeout(tick, 140)
      }
      img.onerror = () => {
        if (stop) return
        timer = setTimeout(tick, 700)
      }
      img.src = url
    }
    tick()
    return () => {
      stop = true
      clearTimeout(timer)
    }
  }, [online])

  useEffect(() => {
    if (!selectedId) return undefined
    let stop = false
    const id = selectedId
    getJSON(`/api/sightings?belonging_id=${encodeURIComponent(id)}&limit=12`)
      .then((rows) => {
        if (!stop) setTrailState({ id, rows })
      })
      .catch(() => {
        if (!stop) setTrailState({ id, rows: [] })
      })
    return () => {
      stop = true
    }
  }, [selectedId])

  const inView = live?.in_view || []
  const others = groupDetections(live?.detections || [])
  const selected = (belongings || []).find((item) => item.id === selectedId) || null
  const cameraAlt = inView.length
    ? `Live camera. Yours in view: ${inView.map((item) => item.name).join(', ')}`
    : 'Live camera from the RecallAI vision loop'

  return (
    <>
      {alerts.length > 0 && (
        <div className="alerts" aria-label="Alerts">
          {alerts.map((alert) => (
            <article
              key={alert.id}
              className={alert.urgency === 'high' ? 'alert high' : 'alert'}
            >
              <Shot className="alert-shot" path={alert.crop_path} alt="" />
              <p>
                <strong>{alert.urgency === 'high' ? 'Left behind? ' : 'Heads up. '}</strong>
                {alert.message}
              </p>
              <button
                type="button"
                className="icon-btn"
                aria-label={`Dismiss alert about ${alert.belonging_name || 'this item'}`}
                onClick={() => onDismiss(alert.id)}
              >
                ×
              </button>
            </article>
          ))}
        </div>
      )}

      <section id="camera" className="section">
        <div className="section-head">
          <p className="kicker">01 — Camera</p>
          <h2>What the vision loop sees</h2>
          <p className="lede">
            This is the annotated webcam from <code>webcam.py</code>: green boxes are
            your belongings, and the other boxes are objects YOLO is tracking.
          </p>
        </div>

        <div className="stage-wrap">
          <div className="stage">
            {online && shownFrame ? (
              <img src={shownFrame} alt={cameraAlt} />
            ) : (
              <div className="stage-empty">
                {serverDown ? (
                  <p>Waiting for the RecallAI server.</p>
                ) : live === null ? (
                  <p>Connecting to the camera…</p>
                ) : (
                  <>
                    <p>The camera is off. Start the vision loop, then this frame fills in.</p>
                    <code>python webcam.py</code>
                  </>
                )}
              </div>
            )}
            {online && (
              <p className="stage-caption">
                {live.width && live.height ? `${live.width}×${live.height}` : 'Live'}
                {' · '}
                {inView.length} of yours
                {' · '}
                {others.reduce((sum, row) => sum + row.count, 0)} objects tracked
                {live.gps ? ' · GPS on' : ''}
              </p>
            )}
          </div>

          <aside className="side">
            <div className="panel">
              <h3>Yours in view</h3>
              {inView.length === 0 ? (
                <p className="muted">
                  {online
                    ? 'No registered belonging is in frame right now.'
                    : 'Names show up here when the camera recognizes something of yours.'}
                </p>
              ) : (
                <ul className="track-list">
                  {inView.map((item) => {
                    const percent = scorePercent(item.score)
                    return (
                      <li key={item.belonging_id || item.name} className="track yours">
                        <div>
                          <b>{item.name}</b>
                          <span>{item.category}</span>
                        </div>
                        {percent != null && (
                          <div className="score" title={`${percent}% match`}>
                            <span style={{ width: `${percent}%` }} />
                            <em>{percent}%</em>
                          </div>
                        )}
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>

            <div className="panel">
              <h3>Objects being tracked</h3>
              {others.length === 0 ? (
                <p className="muted">
                  {online ? 'The frame is empty of detections.' : 'Nothing is being tracked yet.'}
                </p>
              ) : (
                <ul className="track-list">
                  {others.map((row) => (
                    <li key={row.category} className="track">
                      <div>
                        <b>{row.category}</b>
                        <span>{row.count === 1 ? '1 in frame' : `${row.count} in frame`}</span>
                      </div>
                      <em>{Math.round(row.confidence * 100)}%</em>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </aside>
        </div>
      </section>

      <section id="places" className="section">
        <div className="section-head">
          <p className="kicker">02 — Last seen</p>
          <h2>Where each thing was</h2>
          <p className="lede">
            The newest memory for every belonging. A last-place mark is the final
            frame before it left the camera. Open one to follow how it moved.
          </p>
        </div>

        {belongings === null ? (
          <p className="muted">
            {serverDown
              ? 'Locations load once the server is running.'
              : 'Loading belongings…'}
          </p>
        ) : belongings.length === 0 ? (
          <p className="empty">
            Nothing is registered yet. Add a belonging with <code>python register.py</code>.
          </p>
        ) : (
          <div className="loc-grid">
            {belongings.map((item) => {
              const last = item.last
              const dots = (recent || [])
                .filter((row) => row.belonging_id === item.id)
                .slice(0, 8)
                .reverse()
              const pressed = selectedId === item.id
              return (
                <article
                  key={item.id}
                  className={pressed ? 'loc-card selected' : 'loc-card'}
                >
                  <button
                    type="button"
                    className="loc-open"
                    aria-pressed={pressed}
                    onClick={() => setSelectedId((current) => (current === item.id ? null : item.id))}
                  >
                    <Shot
                      className="loc-shot"
                      path={last?.crop_path || last?.image_path}
                      alt={last ? `Last photo of ${item.name}` : `${item.name} has not been seen`}
                    />
                    <span className="loc-copy">
                      <b>{item.name}</b>
                      <span className="kind">{item.category}</span>
                      {last ? (
                        <>
                          <span className={last.event === 'left_view' ? 'badge last' : 'badge'}>
                            {eventLabel(last.event)}
                          </span>
                          <span className="when">{ago(last.timestamp)} · {when(last.timestamp)}</span>
                          {last.place && <span className="place">{last.place}</span>}
                          {last.description && <span className="desc">{last.description}</span>}
                          {last.nearby?.length > 0 && (
                            <span className="near">Near {last.nearby.join(', ')}</span>
                          )}
                        </>
                      ) : (
                        <span className="when">
                          {item.photos
                            ? `${item.photos} reference photo${item.photos === 1 ? '' : 's'}, not seen yet`
                            : 'Needs reference photos'}
                        </span>
                      )}
                      {dots.length > 0 && (
                        <span className="dots" aria-hidden="true">
                          {dots.map((row) => (
                            <i
                              key={row.id}
                              className={row.event === 'left_view' ? 'dot last' : 'dot'}
                              title={`${eventLabel(row.event)} ${ago(row.timestamp)}`}
                            />
                          ))}
                        </span>
                      )}
                    </span>
                  </button>
                  {last?.maps_url && (
                    <a className="map-link" href={last.maps_url} target="_blank" rel="noreferrer">
                      Map · ±{Math.round(last.accuracy)} m
                    </a>
                  )}
                </article>
              )
            })}
          </div>
        )}

        {selected && (
          <div className="trail-wrap">
            <h3>Movement of {selected.name}</h3>
            {trail === null ? (
              <p className="muted">Loading the trail…</p>
            ) : trail.length === 0 ? (
              <p className="muted">No sightings for {selected.name} yet.</p>
            ) : (
              <ol className="trail">
                {trail.map((row) => (
                  <li key={row.id} className="trail-row">
                    <Shot
                      className="trail-shot"
                      path={row.crop_path || row.image_path}
                      alt=""
                    />
                    <div>
                      <span className={row.event === 'left_view' ? 'badge last' : 'badge'}>
                        {eventLabel(row.event)}
                      </span>
                      <span className="when">{ago(row.timestamp)} · {when(row.timestamp)}</span>
                      {row.description && <p className="desc">{row.description}</p>}
                      {row.place && <p className="place">{row.place}</p>}
                      {row.nearby?.length > 0 && (
                        <p className="near">Near {row.nearby.join(', ')}</p>
                      )}
                      {row.image_path && (
                        <a href={mediaUrl(row.image_path)} target="_blank" rel="noreferrer">
                          Full frame
                        </a>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </section>
    </>
  )
}
