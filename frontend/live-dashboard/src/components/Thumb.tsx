import { useState } from 'react';

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
    <span className="thumb-empty" aria-hidden="true">
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

export function CameraIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 8h3l1.5-2h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z" />
      <circle cx="12" cy="13" r="3.5" />
    </svg>
  );
}

export function PinIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 21s-6-5.6-6-11a6 6 0 0 1 12 0c0 5.4-6 11-6 11z" />
      <circle cx="12" cy="10" r="2.2" />
    </svg>
  );
}

export function ClockIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </svg>
  );
}
