import { useEffect, useRef } from 'react';
import { useFocusTrap } from '../hooks/useFocusTrap';

type Props = { src: string; caption?: string; onClose: () => void };

export function Lightbox({ src, caption, onClose }: Props) {
  const layerRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onKeyDown = useFocusTrap(layerRef);
  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  return (
    <div
      ref={layerRef}
      className="lightbox"
      role="dialog"
      aria-modal="true"
      aria-label="Full frame"
      onClick={onClose}
      onKeyDown={onKeyDown}
    >
      <figure onClick={e => e.stopPropagation()}>
        <img src={src} alt={caption || 'Full camera frame'} />
        {caption && <figcaption>{caption}</figcaption>}
        <button ref={closeRef} type="button" className="btn lightbox-close" onClick={onClose}>
          Close
        </button>
      </figure>
    </div>
  );
}
