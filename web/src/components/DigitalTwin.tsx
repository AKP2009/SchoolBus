import { useState, type KeyboardEvent, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { Line, LineChart, ResponsiveContainer, Tooltip, YAxis } from 'recharts';
import { cx } from '@/lib/cx';
import { useMode } from '@/lib/mode';
import { healthStatus, type Status } from '@/lib/status';
import type { HealthScores } from '@/types/domain';
import { Button } from './Button';
import { StatusBadge } from './StatusBadge';
import { useT } from '@/i18n';

export type Subsystem = 'engine' | 'cooling' | 'hydraulics' | 'electrical' | 'undercarriage';

export interface TwinSignal {
  label: string;
  unit: string;
  /** Last 60 minutes, one point per minute, oldest first. */
  points: number[];
}

export interface DigitalTwinProps {
  scores: HealthScores;
  signals?: Partial<Record<Subsystem, TwinSignal[]>>;
  /** Drawing to use; excavator by default. */
  machineType?: 'excavator' | 'wheel_loader' | 'dozer' | 'articulated_truck';
  className?: string;
}

export const SUBSYSTEMS: { key: Subsystem; label: string }[] = [
  { key: 'engine', label: 'Engine' },
  { key: 'cooling', label: 'Cooling' },
  { key: 'hydraulics', label: 'Hydraulics' },
  { key: 'electrical', label: 'Electrical' },
  { key: 'undercarriage', label: 'Undercarriage' },
];

const FILL: Record<Status, string> = {
  ok: 'fill-tint-ok stroke-ok',
  info: 'fill-tint-info stroke-info',
  warning: 'fill-tint-warning stroke-warning',
  critical: 'fill-tint-critical stroke-critical',
  emergency: 'fill-tint-critical stroke-critical',
  offline: 'fill-tint-offline stroke-offline',
  unknown: 'fill-tint-offline stroke-offline',
};

// Flat side view of an excavator in a 600 × 340 viewBox, facing right.
const EXCAVATOR: Record<Subsystem, ReactNode> = {
  undercarriage: (
    <>
      <rect x={50} y={252} width={330} height={62} rx={31} />
      <circle cx={84} cy={283} r={16} className="fill-surface" />
      <circle cx={346} cy={283} r={16} className="fill-surface" />
    </>
  ),
  engine: <path d="M40 168 H170 V244 H40 Z" />,
  cooling: <path d="M176 168 H232 V244 H176 Z" />,
  electrical: <path d="M238 244 V110 Q238 92 256 92 H318 L336 150 V244 Z" />,
  hydraulics: (
    <>
      <path d="M336 206 L446 64 L468 78 L360 222 Z" />
      <path d="M446 64 L472 60 L548 196 L528 206 Z" />
      <path d="M528 206 L566 196 L580 238 Q556 262 522 246 Z" />
    </>
  ),
};

// Flat side view of a dozer, blade on the right: cab (electrical), engine hood, radiator (cooling),
// push arms + lift cylinder + blade (hydraulics), tracks (undercarriage).
const DOZER: Record<Subsystem, ReactNode> = {
  undercarriage: (
    <>
      <rect x={90} y={252} width={350} height={62} rx={31} />
      <circle cx={122} cy={283} r={16} className="fill-surface" />
      <circle cx={408} cy={283} r={16} className="fill-surface" />
    </>
  ),
  electrical: <path d="M110 246 V90 Q110 72 128 72 H228 Q245 72 245 90 V246 Z" />,
  engine: <path d="M250 246 V150 H392 V246 Z" />,
  cooling: <path d="M398 246 V150 H432 V246 Z" />,
  hydraulics: (
    <>
      <path d="M300 262 L470 248 L472 264 L302 278 Z" />
      <path d="M404 150 L482 186 L474 199 L398 165 Z" />
      <path d="M472 116 Q524 205 482 312 L508 314 Q552 205 494 114 Z" />
    </>
  ),
};

// House top per drawing: not a monitored subsystem.
const HOUSE: Record<'excavator' | 'dozer', string> = {
  excavator: 'M40 168 V150 Q40 140 50 140 H232 V168 Z',
  dozer: 'M372 150 V112 H386 V150 Z',
};

function scoreOf(scores: HealthScores, key: Subsystem): number | null {
  return scores[`${key}_score`];
}

function pctText(score: number | null) {
  return score == null ? '—' : `${Math.round(score * 100)}%`;
}

/** Machine health by subsystem (design.md §Digital twin). Tap a region or a row for signals. */
export function DigitalTwin({ scores, signals, machineType = 'excavator', className }: DigitalTwinProps) {
  const drawing = machineType === 'dozer' ? 'dozer' : 'excavator';
  const SHAPES = drawing === 'dozer' ? DOZER : EXCAVATOR;
  const mode = useMode();
  const t = useT();
  const name = (key: Subsystem) => t(`component.${key}`);
  const cab = mode === 'cab';
  const [selected, setSelected] = useState<Subsystem | null>(null);
  const small = cab ? 'text-cab-small' : 'text-office-small';

  const onKey = (key: Subsystem) => (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setSelected(key);
    }
  };

  const sel = selected ? SUBSYSTEMS.find((s) => s.key === selected) : undefined;

  return (
    <section
      className={cx('relative grid grid-cols-[minmax(0,1fr)_auto] items-center overflow-hidden', cab ? 'gap-6' : 'gap-4', className)}
      aria-label={t('twin.aria')}
    >
      <svg viewBox="0 0 600 340" className="w-full min-w-0" role="group" aria-label={t('twin.sideView')}>
        {/* House top: not a monitored subsystem */}
        <path d={HOUSE[drawing]} className="fill-raised stroke-line" strokeWidth={3} />
        {SUBSYSTEMS.map(({ key }) => {
          const score = scoreOf(scores, key);
          const status = healthStatus(score);
          return (
            <g
              key={key}
              role="button"
              tabIndex={0}
              aria-label={`${name(key)}, ${t(`status.${status}`)}, ${t('twin.health', { pct: pctText(score) })}`}
              onClick={() => setSelected(key)}
              onKeyDown={onKey(key)}
              className={cx(
                'cursor-pointer outline-none transition-state focus-visible:opacity-80',
                FILL[status],
              )}
              strokeWidth={selected === key ? 6 : 3}
              strokeLinejoin="round"
            >
              {SHAPES[key]}
            </g>
          );
        })}
      </svg>

      <ul className={cx('flex flex-col', cab ? 'gap-2' : 'gap-1')}>
        {SUBSYSTEMS.map(({ key }) => {
          const score = scoreOf(scores, key);
          return (
            <li key={key}>
              <button
                type="button"
                onClick={() => setSelected(key)}
                aria-pressed={selected === key}
                className={cx(
                  'flex w-full items-center justify-between gap-3 rounded-md border text-left transition-state',
                  selected === key ? 'border-saffron-500 bg-raised' : 'border-transparent hover:bg-raised',
                  cab ? 'min-h-touch-cab px-4 text-cab-body' : 'min-h-touch-office px-3 text-office-body',
                )}
              >
                <span>{name(key)}</span>
                <span className="flex items-center gap-3">
                  <span className="reading text-ink-2">{pctText(score)}</span>
                  <StatusBadge status={healthStatus(score)} />
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      {sel && (
        <aside
          className={cx(
            'absolute inset-y-0 right-0 z-10 flex w-1/2 flex-col border-l bg-surface anim-takeover',
            cab ? 'gap-4 p-6' : 'gap-3 p-4 shadow-float',
          )}
          aria-label={t('twin.signalsAria', { name: name(sel.key) })}
        >
          <div className="flex items-center justify-between gap-3">
            <h3 className={cab ? 'text-cab-h2' : 'text-office-h3'}>{name(sel.key)}</h3>
            <Button variant="ghost" icon={X} onClick={() => setSelected(null)}>
              {t('twin.close')}
            </Button>
          </div>
          <StatusBadge status={healthStatus(scoreOf(scores, sel.key))} label={t('twin.health', { pct: pctText(scoreOf(scores, sel.key)) })} />
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto">
            {(signals?.[sel.key] ?? []).map((sig) => (
              <div key={sig.label}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className={cx('text-ink-2', small)}>{sig.label}</span>
                  <span className={cx('reading', cab ? 'text-cab-h2' : 'text-office-h3')}>
                    {sig.points.at(-1)?.toFixed(1) ?? '—'} <span className={cx('text-ink-2', small)}>{sig.unit}</span>
                  </span>
                </div>
                <div className={cab ? 'h-16' : 'h-10'}>
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={sig.points.map((v, i) => ({ min: i - sig.points.length + 1, v }))}>
                      <YAxis hide domain={['dataMin', 'dataMax']} />
                      <Tooltip
                        formatter={(v) => [`${Number(v).toFixed(1)} ${sig.unit}`, sig.label]}
                        labelFormatter={(_, p) => `${p[0]?.payload.min ?? ''} ${t('unit.min')}`}
                        contentStyle={{
                          fontFamily: 'var(--font-mono)',
                          background: 'var(--surface)',
                          border: '1px solid var(--border)',
                          color: 'var(--text)',
                        }}
                      />
                      <Line type="monotone" dataKey="v" stroke="var(--series-2)" strokeWidth={2} dot={false} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                <p className={cx('text-ink-2', small)}>{t('twin.last60')}</p>
              </div>
            ))}
            {!signals?.[sel.key]?.length && (
              <p className={cx('text-ink-2', small)}>{t('twin.noSignals')}</p>
            )}
          </div>
        </aside>
      )}
    </section>
  );
}
