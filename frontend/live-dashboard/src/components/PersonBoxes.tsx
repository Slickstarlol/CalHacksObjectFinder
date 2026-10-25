import { useEffect, useState } from 'react';
import type { Person } from '../module_bindings/types';
import { boxPercent, iou, percent, type Box, type LiveDetection, type LivePerson } from '../lib/live';
import type { LabelResult } from '../lib/people';
import { NameInput } from './NameInput';

type Props = {
  /** Your people the camera recognizes right now. */
  named: readonly LivePerson[];
  /** Person boxes without a name. */
  unnamed: readonly LiveDetection[];
  width: number | null;
  height: number | null;
  /** Your people (SpacetimeDB), for renaming one in place. */
  people: readonly Person[];
  onRename?: (person: Person, name: string) => Promise<void>;
  /** Names the person in a box (POST /api/live/people). Omitted -> read-only. */
  onLabel?: (bbox: Box, name: string, personId?: number) => Promise<LabelResult>;
};

/** A box frozen where it was double-clicked, so the editor stays put while people move. */
type Editing = { bbox: Box; personId?: number; initial: string };
/** A name just given; shown on that spot until the camera recognizes them. */
type Learning = { personId: number; name: string; bbox: Box; until: number };
type Notice = { tone: 'error' | 'info'; text: string };

const LEARN_MS = 8000;
const NOTICE_MS = 7000;

function place(bbox: Box, width: number | null, height: number | null) {
  const pos = boxPercent(bbox, width, height);
  return pos && { left: `${pos.left}%`, top: `${pos.top}%`, width: `${pos.width}%`, height: `${pos.height}%` };
}

/**
 * People on the live feed. Double-click a box (or focus it and press Enter)
 * to name that person: the camera saves a photo and names them from then on.
 * On a box that already has a name, a new name renames that person, unless
 * it's another of your people's names: then it means "this is them".
 */
export function PersonBoxes({ named, unnamed, width, height, people, onRename, onLabel }: Props) {
  const [editing, setEditing] = useState<Editing | null>(null);
  const [renamed, setRenamed] = useState<Record<number, string>>({});
  const [learning, setLearning] = useState<Learning[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const editable = Boolean(onLabel);

  // Optimistic names end once the camera reports them.
  useEffect(() => {
    setRenamed(prev => {
      const next = { ...prev };
      for (const p of named) if (next[p.personId] === p.name) delete next[p.personId];
      return Object.keys(next).length === Object.keys(prev).length ? prev : next;
    });
    setLearning(prev => {
      const next = prev.filter(l => !named.some(p => p.personId === l.personId));
      return next.length === prev.length ? prev : next;
    });
  }, [named]);

  // A name the camera never matched: say so rather than quietly dropping it.
  useEffect(() => {
    if (!learning.length) return;
    const due = Math.min(...learning.map(l => l.until));
    const t = setTimeout(() => {
      const now = Date.now();
      const late = learning.filter(l => l.until <= now);
      setLearning(prev => prev.filter(l => l.until > now));
      if (late.length) {
        setNotice({
          tone: 'info',
          text: `The camera hasn’t recognized ${late[0].name} yet. Double-click them again to add another photo.`,
        });
      }
    }, Math.max(0, due - Date.now()));
    return () => clearTimeout(t);
  }, [learning]);

  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), NOTICE_MS);
    return () => clearTimeout(t);
  }, [notice]);

  const learn = (res: LabelResult, clicked: Box) =>
    setLearning(prev => [
      ...prev.filter(l => l.personId !== res.personId),
      { personId: res.personId, name: res.name, bbox: res.bbox ?? clicked, until: Date.now() + LEARN_MS },
    ]);

  const save = async (edit: Editing, name: string | null) => {
    setEditing(null);
    if (!name || name === edit.initial || !onLabel) return;
    setNotice(null);
    const id = edit.personId;
    try {
      if (id === undefined) {
        learn(await onLabel(edit.bbox, name), edit.bbox);
        return;
      }
      const other = people.find(p => p.name.toLowerCase() === name.toLowerCase() && Number(p.id) !== id);
      if (other) {
        learn(await onLabel(edit.bbox, other.name, Number(other.id)), edit.bbox);
        return;
      }
      const record = people.find(p => Number(p.id) === id);
      if (!record || !onRename) throw new Error('That person is still syncing. Try again in a moment.');
      setRenamed(prev => ({ ...prev, [id]: name }));
      setLearning(prev => prev.map(l => (l.personId === id ? { ...l, name } : l)));
      await onRename(record, name);
    } catch (err) {
      if (id !== undefined) setRenamed(({ [id]: _gone, ...rest }) => rest);
      setNotice({ tone: 'error', text: err instanceof Error ? err.message : 'Couldn’t save the name.' });
    }
  };

  const boxes = [
    ...named.map(p => ({ key: `p-${p.personId}`, bbox: p.bbox, personId: p.personId as number | undefined, name: renamed[p.personId] ?? p.name, score: p.score })),
    ...(editable ? unnamed : []).map((d, i) => ({ key: `u-${i}`, bbox: d.bbox, personId: undefined, name: '', score: d.confidence })),
  ];

  return (
    <>
      {boxes.map(b => {
        const style = b.bbox && place(b.bbox, width, height);
        if (!b.bbox || !style) return null;
        const bbox = b.bbox;
        const pending = learning.find(l => iou(l.bbox, bbox) > 0.3);
        const name = pending?.name ?? b.name;
        // Unnamed tags stay short so close-together people don't hide each other's names.
        const tag = name ? (
          `${name} ${pending ? '· learning…' : percent(b.score)}`
        ) : (
          <>
            <span className="tag-idle">Who?</span>
            <span className="tag-hint">Double-click to name</span>
          </>
        );
        const cls = `lock lock-person${name ? '' : ' lock-unnamed'}${pending ? ' lock-learning' : ''}`;
        const inner = (
          <>
            <span className="brackets brackets-person" aria-hidden="true" />
            <span className="lock-tag mono" aria-hidden="true">
              {tag}
            </span>
          </>
        );
        if (!editable) {
          return (
            <div key={b.key} className={cls} style={style} aria-hidden="true">
              {inner}
            </div>
          );
        }
        const start = () => setEditing({ bbox, personId: pending?.personId ?? b.personId, initial: name });
        return (
          <button
            key={b.key}
            type="button"
            className={cls}
            style={style}
            title={name ? 'Double-click to rename' : 'Double-click to name this person'}
            aria-label={name ? `${name} in view, press Enter to rename` : 'Unnamed person in view, press Enter to name them'}
            onDoubleClick={start}
            onKeyDown={e => {
              if (e.key === 'Enter' || e.key === 'F2') {
                e.preventDefault();
                start();
              }
            }}
          >
            {inner}
          </button>
        );
      })}
      {editing && place(editing.bbox, width, height) && (
        <div className="name-pop" style={place(editing.bbox, width, height)!}>
          <span className="brackets brackets-person" aria-hidden="true" />
          <NameInput
            initial={editing.initial}
            label={editing.initial ? `New name for ${editing.initial}` : 'Name this person'}
            placeholder="Who is this?"
            onDone={name => void save(editing, name)}
          />
        </div>
      )}
      {notice && (
        <p className={`scene-toast scene-toast-${notice.tone}`} role={notice.tone === 'error' ? 'alert' : 'status'}>
          {notice.text}
        </p>
      )}
    </>
  );
}
