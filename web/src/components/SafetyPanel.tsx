import { ArrowDownRight } from 'lucide-react';
import { cx } from '@/lib/cx';
import { useMode } from '@/lib/mode';
import { STATUS, proximityStatus, tiltStatus, type Status } from '@/lib/status';
import type { FatigueLevel } from '@/types/domain';
import { StatusBadge } from './StatusBadge';

export type Sector = 'front' | 'rear' | 'left' | 'right';

export interface SectorReading {
  /** Nearest person or vehicle in this sector; null = nothing detected. */
  distance_m: number | null;
  approaching?: boolean;
}

export interface SafetyPanelProps {
  sectors: Record<Sector, SectorReading>;
  fatigue: FatigueLevel;
  seatbeltFastened: boolean;
  pitchDeg: number;
  rollDeg: number;
  className?: string;
}

// Geometry in a 400 × 400 viewBox; the machine sits in the middle with the boom pointing up (front).
const SECTOR_SHAPE: Record<Sector, { points: string; x: number; y: number }> = {
  front: { points: '0,0 400,0 240,130 160,130', x: 200, y: 62 },
  rear: { points: '0,400 400,400 240,270 160,270', x: 200, y: 338 },
  left: { points: '0,0 160,130 160,270 0,400', x: 78, y: 200 },
  right: { points: '400,0 240,130 240,270 400,400', x: 322, y: 200 },
};

const SECTOR_WORD: Record<Sector, string> = { front: 'Front', rear: 'Rear', left: 'Left', right: 'Right' };

const FILL: Partial<Record<Status, string>> = {
  warning: 'fill-tint-warning stroke-warning',
  critical: 'fill-tint-critical stroke-critical',
};

const FATIGUE: Record<FatigueLevel, { status: Status; label: string; steps: number }> = {
  low: { status: 'ok', label: 'Low', steps: 1 },
  medium: { status: 'warning', label: 'Medium', steps: 2 },
  high: { status: 'critical', label: 'High', steps: 3 },
};

function SectorZone({ sector, reading }: { sector: Sector; reading: SectorReading }) {
  const status = proximityStatus(reading.distance_m);
  const clear = status === 'ok';
  const shape = SECTOR_SHAPE[sector];
  const Icon = STATUS[status].icon;
  const word = clear ? 'Clear' : status === 'critical' ? 'Too close' : 'Caution';
  return (
    <g>
      <polygon
        points={shape.points}
        className={cx('stroke-[3]', clear ? 'fill-transparent stroke-line' : FILL[status])}
        strokeLinejoin="round"
      />
      <Icon
        x={shape.x - 14}
        y={shape.y - (clear ? 46 : 58)}
        width={28}
        height={28}
        strokeWidth={2}
        className={STATUS[status].text}
        aria-hidden
      />
      {!clear && reading.distance_m != null && (
        <text x={shape.x} y={shape.y + 6} textAnchor="middle" className="reading fill-ink text-[34px] font-medium">
          {reading.distance_m.toFixed(1)} m
        </text>
      )}
      <text
        x={shape.x}
        y={shape.y + (clear ? 4 : 34)}
        textAnchor="middle"
        className="fill-ink-2 text-[22px]"
      >
        {clear ? `${SECTOR_WORD[sector]} clear` : reading.approaching ? `${word}, closing` : word}
      </text>
    </g>
  );
}

function MachineOutline() {
  // Flat top-down excavator: tracks, upper body, cab on the left, boom and bucket forward.
  return (
    <g className="fill-raised stroke-ink-2" strokeWidth={3} strokeLinejoin="round">
      <rect x={165} y={185} width={18} height={75} rx={4} />
      <rect x={217} y={185} width={18} height={75} rx={4} />
      <rect x={172} y={170} width={56} height={70} rx={8} />
      <rect x={176} y={176} width={20} height={24} rx={3} className="fill-surface" />
      <rect x={192} y={128} width={16} height={46} rx={3} />
      <rect x={184} y={110} width={32} height={20} rx={4} />
    </g>
  );
}

/** Top-down machine with four camera sectors, fatigue, seatbelt and tilt (design.md §Safety panel). */
export function SafetyPanel({ sectors, fatigue, seatbeltFastened, pitchDeg, rollDeg, className }: SafetyPanelProps) {
  const mode = useMode();
  const cab = mode === 'cab';
  const f = FATIGUE[fatigue];
  const tilt = tiltStatus(pitchDeg, rollDeg);
  const nearest = (Object.keys(sectors) as Sector[])
    .filter((s) => sectors[s].distance_m != null)
    .sort((a, b) => (sectors[a].distance_m ?? 0) - (sectors[b].distance_m ?? 0))[0];
  const summary = nearest
    ? `Nearest person ${SECTOR_WORD[nearest].toLowerCase()}, ${sectors[nearest].distance_m?.toFixed(1)} metres`
    : 'All sectors clear';

  const small = cab ? 'text-cab-small' : 'text-office-small';
  return (
    <section className={cx('flex flex-col', cab ? 'gap-4' : 'gap-3', className)} aria-label="Safety">
      <svg viewBox="0 0 400 400" role="img" aria-label={summary} className="mx-auto aspect-square w-full max-w-[400px]">
        {(Object.keys(SECTOR_SHAPE) as Sector[]).map((s) => (
          <SectorZone key={s} sector={s} reading={sectors[s]} />
        ))}
        <MachineOutline />
      </svg>

      <dl className={cx('grid grid-cols-3', cab ? 'gap-4' : 'gap-3')}>
        <div className="flex flex-col gap-2">
          <dt className={cx('text-ink-2', small)}>Fatigue</dt>
          <dd className="flex flex-col gap-2">
            <div className="flex gap-1" aria-hidden>
              {[1, 2, 3].map((step) => (
                <span
                  key={step}
                  className={cx(
                    'flex-1 rounded-sm',
                    cab ? 'h-3' : 'h-2',
                    step <= f.steps ? STATUS[f.status].bg : 'bg-raised',
                  )}
                />
              ))}
            </div>
            <StatusBadge status={f.status} label={f.label} />
          </dd>
        </div>
        <div className="flex flex-col gap-2">
          <dt className={cx('text-ink-2', small)}>Seatbelt</dt>
          <dd>
            <StatusBadge
              status={seatbeltFastened ? 'ok' : 'warning'}
              label={seatbeltFastened ? 'Fastened' : 'Unfastened'}
            />
          </dd>
        </div>
        <div className="flex flex-col gap-2">
          <dt className={cx('text-ink-2', small)}>Tilt</dt>
          <dd className="flex flex-col gap-2">
            <span className={cx('flex items-center gap-2', small)}>
              <ArrowDownRight size={cab ? 22 : 16} aria-hidden className="shrink-0 text-ink-2" />
              <span className="flex flex-col">
                <span>
                  <span className="text-ink-2">Pitch </span>
                  <span className="reading">{Math.abs(pitchDeg).toFixed(0)}°</span>
                </span>
                <span>
                  <span className="text-ink-2">Roll </span>
                  <span className="reading">{Math.abs(rollDeg).toFixed(0)}°</span>
                </span>
              </span>
            </span>
            <StatusBadge status={tilt} label={tilt === 'ok' ? 'Level' : tilt === 'warning' ? 'Steep' : 'Tip risk'} />
          </dd>
        </div>
      </dl>
    </section>
  );
}
