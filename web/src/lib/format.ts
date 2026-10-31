// Numbers as engineers read them: units always, sensible precision, a real minus.

const MINUS = '−';

export function signed(value: number, digits = 1): string {
  const text = Math.abs(value).toFixed(digits);
  if (Number(text) === 0) return (0).toFixed(digits);
  return `${value < 0 ? MINUS : '+'}${text}`;
}

export function fixed(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const text = value.toFixed(digits);
  return value < 0 ? MINUS + text.slice(1) : text;
}

export function degrees(radians: number | null | undefined, digits = 1): string {
  if (radians === null || radians === undefined || !Number.isFinite(radians)) return '—';
  return `${signed((radians * 180) / Math.PI, digits)}°`;
}

export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const a = Math.abs(value);
  if (a >= 1e9) return `${fixed(value / 1e9, 1)}B`;
  if (a >= 1e6) return `${fixed(value / 1e6, a >= 1e8 ? 0 : 1)}M`;
  if (a >= 1e3) return `${fixed(value / 1e3, a >= 1e5 ? 0 : 1)}K`;
  return fixed(value, a >= 100 ? 0 : 1);
}

export function percent(share: number | null | undefined, digits = 0): string {
  if (share === null || share === undefined || !Number.isFinite(share)) return '—';
  return `${(share * 100).toFixed(digits)}%`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—';
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  if (h) return `${h} h ${String(m).padStart(2, '0')} min`;
  if (m) return `${m} min ${String(r).padStart(2, '0')} s`;
  return `${r} s`;
}

/** A design value with its unit, e.g. "+42 mm", "25.5 N/mm". */
export function designValue(value: number, unit: string, kind: string): string {
  if (kind === 'rod') return `${signed(value, 0)} mm`;
  if (unit === 'N/mm') return `${fixed(value, 1)} N/mm`;
  if (unit === 'mm') return `${fixed(value, 1)} mm`;
  return `${fixed(value, 0)} ${unit}`;
}

/** 'knee_pitch_left' -> 'L knee', 'hip_roll_right' -> 'R hip roll', 'waist_yaw' -> 'Waist yaw'. */
export function jointLabel(name: string): string {
  const words = name.split('_');
  const side = words.includes('left') ? 'L' : words.includes('right') ? 'R' : '';
  let rest = words.filter((w) => w !== 'left' && w !== 'right');
  if (rest[0] === 'knee' || rest[0] === 'elbow') rest = [rest[0]];
  const text = rest.join(' ');
  return side ? `${side} ${text}` : text.charAt(0).toUpperCase() + text.slice(1);
}
