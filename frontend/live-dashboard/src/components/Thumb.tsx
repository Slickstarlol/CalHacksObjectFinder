import { useState } from 'react';
import { CameraIcon } from './Icons';

type Props = {
  src: string;
  alt: string;
  className?: string;
  onClick?: () => void;
};

/** Image with a neutral placeholder when the URL is empty or fails to load. */
export function Thumb({ src, alt, className = '', onClick }: Props) {
  const [failed, setFailed] = useState<string | null>(null);
  const broken = !src || failed === src;
  const content = broken ? (
    <span className="thumb-empty">
      <CameraIcon />
    </span>
  ) : (
    <img src={src} alt={alt} loading="lazy" onError={() => setFailed(src)} />
  );
  if (onClick && !broken) {
    return (
      <button type="button" className={`thumb ${className}`} onClick={onClick} aria-label={`Open ${alt}`}>
        {content}
      </button>
    );
  }
  return <span className={`thumb ${className}`}>{content}</span>;
}
