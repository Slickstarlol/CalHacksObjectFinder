import { useEffect, type RefObject } from 'react';

/**
 * Feeds the cursor position to the page-grid lens (styles.css `.gridbg`).
 * Writes --mx / --my straight onto the element once per animation frame, so
 * moving the mouse never re-renders React. Toggles `lens-on` while the
 * cursor is over the page.
 */
export function useCursorLens(ref: RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (typeof window === 'undefined' || typeof requestAnimationFrame === 'undefined') return;
    let frame = 0;
    let x = 0;
    let y = 0;
    const paint = () => {
      frame = 0;
      const el = ref.current;
      if (!el) return;
      el.style.setProperty('--mx', `${x}px`);
      el.style.setProperty('--my', `${y}px`);
      el.classList.add('lens-on');
    };
    const onMove = (e: MouseEvent) => {
      x = e.clientX;
      y = e.clientY;
      if (!frame) frame = requestAnimationFrame(paint);
    };
    const onLeave = () => ref.current?.classList.remove('lens-on');
    window.addEventListener('mousemove', onMove, { passive: true });
    document.documentElement.addEventListener('mouseleave', onLeave);
    return () => {
      window.removeEventListener('mousemove', onMove);
      document.documentElement.removeEventListener('mouseleave', onLeave);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [ref]);
}
