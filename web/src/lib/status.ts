import {
  CircleCheck,
  CircleHelp,
  Info,
  OctagonAlert,
  Siren,
  TriangleAlert,
  WifiOff,
  type LucideIcon,
} from 'lucide-react';

/** Every status the UI can show. Severity levels mirror `severity_level`; ok/offline/unknown are UI-only. */
export type Status = 'ok' | 'info' | 'warning' | 'critical' | 'emergency' | 'offline' | 'unknown';

interface StatusMeta {
  icon: LucideIcon;
  word: string;
  /** Tailwind classes, all backed by tokens. */
  text: string;
  bg: string;
  tint: string;
  border: string;
}

export const STATUS: Record<Status, StatusMeta> = {
  ok: { icon: CircleCheck, word: 'OK', text: 'text-ok', bg: 'bg-ok', tint: 'bg-tint-ok', border: 'border-ok' },
  info: { icon: Info, word: 'Note', text: 'text-info', bg: 'bg-info', tint: 'bg-tint-info', border: 'border-info' },
  warning: {
    icon: TriangleAlert,
    word: 'Warning',
    text: 'text-warning',
    bg: 'bg-warning',
    tint: 'bg-tint-warning',
    border: 'border-warning',
  },
  critical: {
    icon: OctagonAlert,
    word: 'Critical',
    text: 'text-critical',
    bg: 'bg-critical',
    tint: 'bg-tint-critical',
    border: 'border-critical',
  },
  emergency: {
    icon: Siren,
    word: 'Emergency',
    text: 'text-critical',
    bg: 'bg-critical',
    tint: 'bg-tint-critical',
    border: 'border-critical',
  },
  offline: {
    icon: WifiOff,
    word: 'Offline',
    text: 'text-offline',
    bg: 'bg-offline',
    tint: 'bg-tint-offline',
    border: 'border-offline',
  },
  unknown: {
    icon: CircleHelp,
    word: 'Unknown',
    text: 'text-offline',
    bg: 'bg-offline',
    tint: 'bg-tint-offline',
    border: 'border-offline',
  },
};

/** Health score bands from docs/models.md §Digital twin: ≥ 0.75 OK, 0.5–0.75 warning, < 0.5 critical. */
export function healthStatus(score: number | null | undefined): Status {
  if (score == null || Number.isNaN(score)) return 'unknown';
  if (score >= 0.75) return 'ok';
  if (score >= 0.5) return 'warning';
  return 'critical';
}

/** Proximity zones from docs/features.md §2.2: < 3 m critical, 3–7 m warning, otherwise clear. */
export function proximityStatus(distanceM: number | null | undefined): Status {
  if (distanceM == null) return 'ok';
  if (distanceM < 3) return 'critical';
  if (distanceM <= 7) return 'warning';
  return 'ok';
}

/** Tilt thresholds from docs/features.md §2.4: > 15° warning, > 25° critical. */
export function tiltStatus(pitchDeg: number, rollDeg: number): Status {
  const worst = Math.max(Math.abs(pitchDeg), Math.abs(rollDeg));
  if (worst > 25) return 'critical';
  if (worst > 15) return 'warning';
  return 'ok';
}
