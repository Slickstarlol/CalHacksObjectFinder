import { useEffect, useRef, useState } from 'react';
import type { Sighting } from '../module_bindings/types';
import type { ApiSighting } from '../lib/ask';
import { describeStep } from '../lib/ask';
import { parseAnswer } from '../lib/citations';
import { frameUrl, thumbUrl } from '../lib/sightings';
import { resolveUrl } from '../lib/url';
import { formatRelative, microsToDate } from '../lib/time';
import type { AskEntry, OpenImage } from './types';

type Props = {
  entries: AskEntry[];
  pending: boolean;
  onAsk: (question: string) => void;
  onClear: () => void;
  sightingsById: Map<string, Sighting>;
  apiBase: string;
  now: Date;
  onOpenImage: OpenImage;
};

const SUGGESTIONS = ['Where are my keys?', 'When did I last see my bag?', 'What did I leave behind today?'];

export function AskPanel({ entries, pending, onAsk, onClear, sightingsById, apiBase, now, onOpenImage }: Props) {
  const [q, setQ] = useState('');
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'nearest' });
  }, [entries]);

  const submit = (text: string) => {
    if (!text.trim() || pending) return;
    onAsk(text);
    setQ('');
  };

  return (
    <section className="panel ask" aria-labelledby="ask-title">
      <header className="panel-head">
        <h2 id="ask-title">Ask RecallAI</h2>
        {entries.length > 0 && (
          <button type="button" className="btn btn-link" onClick={onClear} disabled={pending}>
            Clear
          </button>
        )}
      </header>

      {entries.length === 0 ? (
        <div className="ask-empty">
          <p className="muted">Ask Grok about anything the camera has seen.</p>
          <div className="suggestions">
            {SUGGESTIONS.map(s => (
              <button key={s} type="button" className="chip chip-btn" onClick={() => submit(s)}>
                {s}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="ask-log" aria-live="polite">
          {entries.map(e => (
            <div key={e.id} className="qa">
              <p className="qa-q">{e.question}</p>
              {e.status === 'pending' && (
                <p className="qa-a qa-pending">
                  <span className="spinner" aria-hidden="true" /> Grok is looking through your memories…
                </p>
              )}
              {e.status === 'error' && <p className="qa-a qa-error" role="alert">{e.error}</p>}
              {e.status === 'done' && e.response && (
                <Answer
                  entry={e}
                  sightingsById={sightingsById}
                  apiBase={apiBase}
                  now={now}
                  onOpenImage={onOpenImage}
                />
              )}
            </div>
          ))}
          <div ref={endRef} />
        </div>
      )}

      <form
        className="ask-form"
        onSubmit={ev => {
          ev.preventDefault();
          submit(q);
        }}
      >
        <input
          type="text"
          value={q}
          onChange={ev => setQ(ev.target.value)}
          placeholder="Where did I leave my water bottle?"
          aria-label="Question for RecallAI"
          maxLength={500}
        />
        <button type="submit" className="btn btn-primary" disabled={pending || !q.trim()}>
          Ask
        </button>
      </form>
    </section>
  );
}

function apiThumb(row: ApiSighting | undefined, apiBase: string): string {
  if (!row) return '';
  return resolveUrl(row.crop_path || row.image_path || '', apiBase);
}

function Answer({
  entry,
  sightingsById,
  apiBase,
  now,
  onOpenImage,
}: {
  entry: AskEntry;
  sightingsById: Map<string, Sighting>;
  apiBase: string;
  now: Date;
  onOpenImage: OpenImage;
}) {
  const res = entry.response!;
  const apiRows = new Map(res.sightings.map(r => [String(r.id), r]));
  const segments = parseAnswer(res.answer);
  return (
    <div className="qa-a">
      <p className="qa-text">
        {segments.map((seg, i) => {
          if (seg.kind === 'text') return <span key={i}>{seg.text}</span>;
          const live = sightingsById.get(seg.id);
          const apiRow = apiRows.get(seg.id);
          const thumb = live ? thumbUrl(live, apiBase) : apiThumb(apiRow, apiBase);
          const full = live ? frameUrl(live, apiBase) : resolveUrl(apiRow?.image_path || apiRow?.crop_path || '', apiBase);
          const label = live
            ? `${live.belongingName || live.objectName}, ${formatRelative(microsToDate(live.seenAtMicros), now)}`
            : `Sighting #${seg.id}`;
          return (
            <button
              key={i}
              type="button"
              className={`cite${thumb ? '' : ' cite-plain'}`}
              title={label}
              disabled={!full}
              onClick={() => full && onOpenImage(full, live?.description || label)}
              data-testid="citation"
            >
              {thumb && <img src={thumb} alt="" />}
              <span>#{seg.id}</span>
            </button>
          );
        })}
      </p>
      {res.steps.length > 0 && (
        <details className="steps">
          <summary>
            How Grok found this ({res.steps.length} step{res.steps.length === 1 ? '' : 's'})
          </summary>
          <ol>
            {res.steps.map((s, i) => (
              <li key={i}>
                <code>{describeStep(s)}</code>
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}
