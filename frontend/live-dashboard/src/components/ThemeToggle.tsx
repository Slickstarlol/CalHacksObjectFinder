import { useEffect, useId, useState } from 'react';
import { flushSync } from 'react-dom';
import { currentTheme, saveTheme, type Theme } from '../lib/theme';

type ViewTransitionDoc = Document & { startViewTransition?: (update: () => void) => unknown };

/** Sun / moon button that flips light and dark mode. */
export function ThemeToggle({ className = '' }: { className?: string }) {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const maskId = `theme-mask${useId().replace(/:/g, '')}`;
  const dark = theme === 'dark';

  // Until someone picks a theme, follow the system setting as it changes.
  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (!mq?.addEventListener) return;
    const onChange = () => setTheme(currentTheme());
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  const flip = (origin: { x: number; y: number }) => {
    const next: Theme = currentTheme() === 'dark' ? 'light' : 'dark';
    const apply = () => {
      saveTheme(next);
      flushSync(() => setTheme(next));
    };
    const doc = document as ViewTransitionDoc;
    const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (!doc.startViewTransition || still) {
      apply();
      return;
    }
    // The new theme grows in a circle out of the button.
    const root = document.documentElement.style;
    root.setProperty('--flip-x', `${origin.x}px`);
    root.setProperty('--flip-y', `${origin.y}px`);
    doc.startViewTransition(apply);
  };

  return (
    <button
      type="button"
      className={`theme-toggle${dark ? ' is-dark' : ''} ${className}`.trim()}
      aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
      title={dark ? 'Light mode' : 'Dark mode'}
      onClick={e => {
        const r = e.currentTarget.getBoundingClientRect();
        flip({ x: r.left + r.width / 2, y: r.top + r.height / 2 });
      }}
    >
      <svg className="theme-icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <mask id={maskId}>
          <rect width="24" height="24" fill="#fff" />
          <circle className="theme-bite" r="6.5" fill="#000" />
        </mask>
        <circle className="theme-core" cx="12" cy="12" fill="currentColor" mask={`url(#${maskId})`} />
        <g className="theme-rays" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M12 1.8v2.2M12 20v2.2M1.8 12h2.2M20 12h2.2M4.8 4.8l1.5 1.5M17.7 17.7l1.5 1.5M4.8 19.2l1.5-1.5M17.7 6.3l1.5-1.5" />
        </g>
      </svg>
    </button>
  );
}
