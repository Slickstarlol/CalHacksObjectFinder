/**
 * Light / dark. The toggle sets <html data-theme>, which the CSS tokens
 * (light-dark()) follow; without a choice the page follows the system
 * setting. The choice is remembered in this browser only.
 */
export type Theme = 'light' | 'dark';

const KEY = 'recallai.theme';

function systemTheme(): Theme {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function currentTheme(): Theme {
  const set = document.documentElement.dataset.theme;
  return set === 'light' || set === 'dark' ? set : systemTheme();
}

/** Re-apply the remembered choice before the first paint, so it doesn't flash. */
export function applyStoredTheme(): void {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === 'light' || saved === 'dark') document.documentElement.dataset.theme = saved;
  } catch {
    /* storage blocked: follow the system */
  }
}

export function saveTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* private mode: still switches, just isn't remembered */
  }
}
