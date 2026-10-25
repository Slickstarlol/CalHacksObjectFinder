type Props = { src: string; caption?: string; onClose: () => void };

export function Lightbox({ src, caption, onClose }: Props) {
  return (
    <div className="lightbox" role="dialog" aria-modal="true" aria-label="Full frame" onClick={onClose}>
      <figure onClick={e => e.stopPropagation()}>
        <img src={src} alt={caption || 'Full camera frame'} />
        {caption && <figcaption>{caption}</figcaption>}
        <button type="button" className="btn lightbox-close" onClick={onClose}>
          Close
        </button>
      </figure>
    </div>
  );
}
