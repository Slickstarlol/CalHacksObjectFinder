import { useEffect, useRef, useState } from 'react';
import type { Sighting } from '../module_bindings/types';
import type { ApiSighting } from '../lib/ask';
import { describeStep } from '../lib/ask';
import { parseAnswer } from '../lib/citations';
import { frameUrl, thumbUrl } from '../lib/sightings';
import { resolveUrl } from '../lib/url';
import { formatRelative, formatShortAgo, microsToDate } from '../lib/time';
import { ArrowIcon, SparkIcon } from './Icons';
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
    <section className="panel ask" id="ask" aria-labelledby="ask-title">
      <header className="panel-head">
        <h2 id="ask-title">
          <span className="accent-icon">
            <SparkIcon />
          </span>
          Ask RecallAI
        </h2>
        {entries.length > 0 && (
          <button type="button" className="btn btn-link" onClick={onClear} disabled={pending}>
            Clear
          </button>
        )}
      </header>

      {entries.length === 0 ? (
        <p className="muted small">Ask Grok about anything the camera has seen. Answers cite the sightings they come from.</p>
      ) : (
        <div className="ask-log" aria-live="polite">
          {entries.map(e => (
            <div key={e.id} className="qa">
              <p className="qa-q pop">{e.question}</p>
              {e.status === 'pending' && (
                <p className="qa-a qa-pending pop">
                  <span className="typing" aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </span>
                  Grok is looking through your memories…
                </p>
              )}
              {e.status === 'error' && (
                <p className="qa-a qa-error pop" role="alert">
                  {e.error}
                </p>
              )}
              {e.status === 'done' && e.response && (
                <Answer entry={e} sightingsById={sightingsById} apiBase={apiBase} now={now} onOpenImage={onOpenImage} />
              )}
            </div>
          ))}
          <div ref={endRef} />
        </div>
      )}

      <div className="suggestions">
        {SUGGESTIONS.map(s => (
          <button key={s} type="button" className="chip-btn" onClick={() => submit(s)} disabled={pending}>
            {s}
          </button>
        ))}
      </div>

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
        <button type="submit" className="btn-send" aria-label="Ask" disabled={pending || !q.trim()}>
          <ArrowIcon />
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
    <div className="qa-a pop">
      <p className="qa-text">
        {segments.map((seg, i) => {
          if (seg.kind === 'text') return <span key={i}>{seg.text}</span>;
          const live = sightingsById.get(seg.id);
          const apiRow = apiRows.get(seg.id);
          const thumb = live ? thumbUrl(live, apiBase) : apiThumb(apiRow, apiBase);
          const full = live ? frameUrl(live, apiBase) : resolveUrl(apiRow?.image_path || apiRow?.crop_path || '', apiBase);
          const at = live ? microsToDate(live.seenAtMicros) : null;
          const label = live
            ? `${live.belongingName || live.objectName}, ${formatRelative(at!, now)}`
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
              {at && (
                <span className="cite-age" aria-hidden="true">
                  {formatShortAgo(at, now)}
                </span>
              )}
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
