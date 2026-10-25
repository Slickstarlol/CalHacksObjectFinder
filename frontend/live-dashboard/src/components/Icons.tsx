/** Small inline icons. All decorative (aria-hidden); give the control a label instead. */

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" fill="none" aria-hidden="true" className="logo">
      <rect x="1" y="1" width="26" height="26" rx="6" fill="var(--accent)" />
      <path
        d="M7 11V8.5A1.5 1.5 0 0 1 8.5 7H11M17 7h2.5A1.5 1.5 0 0 1 21 8.5V11M21 17v2.5a1.5 1.5 0 0 1-1.5 1.5H17M11 21H8.5A1.5 1.5 0 0 1 7 19.5V17"
        stroke="#fff"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
      <circle cx="14" cy="14" r="2.6" fill="#fff" />
    </svg>
  );
}

/** The RecallAI "speaker" avatar beside recall bubbles. */
export function RecallAvatar({ size = 26 }: { size?: number }) {
  return (
    <span className="avatar" style={{ width: size, height: size }} aria-hidden="true">
      <svg width={size * 0.54} height={size * 0.54} viewBox="0 0 28 28" fill="none">
        <path
          d="M7 11V8.5A1.5 1.5 0 0 1 8.5 7H11M17 7h2.5A1.5 1.5 0 0 1 21 8.5V11M21 17v2.5a1.5 1.5 0 0 1-1.5 1.5H17M11 21H8.5A1.5 1.5 0 0 1 7 19.5V17"
          stroke="#fff"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
        <circle cx="14" cy="14" r="3" fill="#fff" />
      </svg>
    </span>
  );
}

export function PinIcon({ size = 14 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
      <path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21Z" />
      <circle cx="12" cy="9.5" r="2.5" />
    </svg>
  );
}

export function SparkIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z" />
    </svg>
  );
}

export function ArrowIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

export function CloseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

export function HourglassIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 2h12M6 22h12M7 2v4a5 5 0 0 0 10 0V2M7 22v-4a5 5 0 0 1 10 0v4" />
    </svg>
  );
}

export function CameraIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 10l5-3v10l-5-3" />
      <rect x="3" y="6" width="12" height="12" rx="2" />
    </svg>
  );
}

/** Two drifting sine strokes under the page heading (pure decoration). */
export function Wave() {
  const path = (step: number, amp: number) => {
    let d = `M0 7 Q ${step / 2} ${7 - amp} ${step} 7`;
    for (let x = step * 2; x <= 2400; x += step) d += ` T ${x} 7`;
    return d;
  };
  return (
    <div className="wave" aria-hidden="true">
      <svg className="wave-1" width="2400" height="14" viewBox="0 0 2400 14">
        <path d={path(60, 7)} fill="none" stroke="var(--accent)" strokeWidth="2" />
      </svg>
      <svg className="wave-2" width="2400" height="14" viewBox="0 0 2400 14">
        <path d={path(120, 6)} fill="none" stroke="var(--sand)" strokeWidth="2" />
      </svg>
    </div>
  );
}
