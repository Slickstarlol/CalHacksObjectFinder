import { useEffect, useState } from 'react';
import type { Person } from '../module_bindings/types';
import { boxPercent, iou, percent, type Box, type LiveDetection, type LivePerson } from '../lib/live';
import type { LabelResult } from '../lib/people';

/** One person the camera sees, as the live panel lists them. */
export type PersonView = {
  /** "p-<person id>" for someone recognized, "u-<n>" for the n-th unnamed person from the left. */
  key: string;
  bbox: Box | null;
  personId?: number;
  /** '' until they have a name. */
  name: string;
  /** Their name, or "Person 2". */
  label: string;
  /** Match score when recognized, YOLO confidence otherwise (0..1). */
  score: number;
  /** Just named: the camera hasn't recognized them yet. */
  learning: boolean;
};

/** A person being named, with their box frozen so the row stays put while people move. */
export type Editing = { key: string; bbox: Box | null; personId?: number; initial: string; label: string };
type Learning = { personId: number; name: string; bbox: Box; until: number };
export type Notice = { tone: 'error' | 'info'; text: string };

type Options = {
  named: readonly LivePerson[];
  unnamed: readonly LiveDetection[];
  /** Your people (SpacetimeDB), for renaming one in place. */
  people: readonly Person[];
  onRename?: (person: Person, name: string) => Promise<void>;
  /** Names the person in a box (POST /api/live/people). Omitted -> read-only. */
  onLabel?: (bbox: Box, name: string, personId?: number) => Promise<LabelResult>;
};

const LEARN_MS = 8000;
const NOTICE_MS = 7000;

/**
 * Naming the people in view. A new name on someone recognized renames that
 * person, unless it's another of your people's names: then it means "this is
 * them" and the camera learns their look from this box.
 */
export function usePeopleNaming({ named, unnamed, people, onRename, onLabel }: Options) {
  const [editing, setEditing] = useState<Editing | null>(null);
  const [renamed, setRenamed] = useState<Record<number, string>>({});
  const [learning, setLearning] = useState<Learning[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);

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

  const learningAt = (bbox: Box | null) => (bbox ? learning.find(l => iou(l.bbox, bbox) > 0.3) : undefined);
  // Left to right, so "Person 1" stays the same person while nobody crosses.
  const strangers = [...unnamed].sort((a, b) => (a.bbox?.[0] ?? 0) - (b.bbox?.[0] ?? 0));
  const views: PersonView[] = [
    ...named.map(p => {
      const l = learningAt(p.bbox);
      const name = l?.name ?? renamed[p.personId] ?? p.name;
      return { key: `p-${p.personId}`, bbox: p.bbox, personId: l?.personId ?? p.personId, name, label: name, score: p.score, learning: Boolean(l) };
    }),
    ...strangers.map((d, n) => {
      const l = learningAt(d.bbox);
      return {
        key: `u-${n}`,
        bbox: d.bbox,
        personId: l?.personId,
        name: l?.name ?? '',
        label: l?.name ?? `Person ${n + 1}`,
        score: d.confidence,
        learning: Boolean(l),
      };
    }),
  ];

  const learn = (res: LabelResult, clicked: Box) =>
    setLearning(prev => [
      ...prev.filter(l => l.personId !== res.personId),
      { personId: res.personId, name: res.name, bbox: res.bbox ?? clicked, until: Date.now() + LEARN_MS },
    ]);

  const finish = async (name: string | null) => {
    const edit = editing;
    setEditing(null);
    if (!edit || !name || name === edit.initial || !onLabel) return;
    setNotice(null);
    const id = edit.personId;
    try {
      if (id === undefined) {
        if (!edit.bbox) throw new Error('That person isn’t in view any more.');
        learn(await onLabel(edit.bbox, name), edit.bbox);
        return;
      }
      const other = people.find(p => p.name.toLowerCase() === name.toLowerCase() && Number(p.id) !== id);
      if (other && edit.bbox) {
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

  const start = (v: PersonView) => {
    if (onLabel) setEditing({ key: v.key, bbox: v.bbox, personId: v.personId, initial: v.name, label: v.label });
  };

  return { views, editing, start, finish, notice, editable: Boolean(onLabel) };
}

/**
 * Read-only names on the camera picture. Unnamed people get no tag; the one
 * whose row is hovered or being named is outlined so you can tell who's who.
 */
export function PersonTags({
  views,
  focus,
  width,
  height,
}: {
  views: readonly PersonView[];
  /** The person whose row is hovered, focused or being named (frozen box if they left). */
  focus: { key: string; bbox: Box | null } | null;
  width: number | null;
  height: number | null;
}) {
  const shown = views.filter(v => v.name || v.key === focus?.key);
  if (focus && !views.some(v => v.key === focus.key)) {
    shown.push({ key: focus.key, bbox: focus.bbox, name: '', label: '', score: 0, learning: false });
  }
  return (
    <>
      {shown.map(v => {
        const pos = boxPercent(v.bbox, width, height);
        if (!pos) return null;
        const on = v.key === focus?.key;
        return (
          <div
            key={v.key}
            className={`lock lock-person${on ? ' is-focus' : ''}${v.learning ? ' lock-learning' : ''}`}
            style={{ left: `${pos.left}%`, top: `${pos.top}%`, width: `${pos.width}%`, height: `${pos.height}%` }}
            aria-hidden="true"
          >
            <span className="brackets brackets-person" />
            {v.label && (
              <span className="lock-tag mono">
                {v.name ? `${v.label} ${v.learning ? '· learning…' : percent(v.score)}` : v.label}
              </span>
            )}
          </div>
        );
      })}
    </>
  );
}
