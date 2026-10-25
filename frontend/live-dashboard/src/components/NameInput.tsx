import { useEffect, useRef, useState } from 'react';

type Props = {
  initial: string;
  /** Accessible name, e.g. "New name for Mom". */
  label: string;
  placeholder?: string;
  /** Called once: the trimmed name (Enter or leaving the field), or null (Escape). */
  onDone: (name: string | null) => void;
};

/** The inline name editor shared by the People panel and the live feed. */
export function NameInput({ initial, label, placeholder, onDone }: Props) {
  const [draft, setDraft] = useState(initial);
  const input = useRef<HTMLInputElement>(null);
  const done = useRef(false);

  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);

  const finish = (save: boolean) => {
    if (done.current) return;
    done.current = true;
    onDone(save ? draft.trim() : null);
  };

  return (
    <input
      ref={input}
      className="name-input"
      value={draft}
      maxLength={80}
      placeholder={placeholder}
      aria-label={label}
      onChange={e => setDraft(e.target.value)}
      onKeyDown={e => {
        if (e.key === 'Enter') {
          e.preventDefault();
          finish(true);
        } else if (e.key === 'Escape') {
          e.stopPropagation(); // don't also close a drawer
          finish(false);
        }
      }}
      onBlur={() => finish(true)}
    />
  );
}
