import { ArrowDown, ArrowUp } from 'lucide-react';
import { cx } from '@/lib/cx';
import { useMode } from '@/lib/mode';
import { STATUS, type Status } from '@/lib/status';
import { StatusBadge } from './StatusBadge';
import { useT } from '@/i18n';

export type Trend = 'rising' | 'falling' | 'steady';

export interface GaugeProps {
  label: string;
  value: number | null;
  unit: string;
  min: number;
  max: number;
  /** Normal operating range, drawn as a faint band on the track. */
  normal: [number, number];
  /** Defaults to OK inside the normal range and Warning outside it; pass Critical from the rule engine. */
  status?: Status;
  trend?: Trend;
  decimals?: number;
  className?: string;
}

const pct = (v: number, min: number, max: number) => Math.min(100, Math.max(0, ((v - min) / (max - min)) * 100));

/** Horizontal bar gauge (design.md §Gauges): easier to glance than a dial. */
export function Gauge({
  label,
  value,
  unit,
  min,
  max,
  normal,
  status,
  trend,
  decimals = 0,
  className,
}: GaugeProps) {
  const mode = useMode();
  const t = useT();
  const cab = mode === 'cab';
  const s: Status =
    status ?? (value == null ? 'unknown' : value >= normal[0] && value <= normal[1] ? 'ok' : 'warning');
  const meta = STATUS[s];
  const bandLeft = pct(normal[0], min, max);
  const bandWidth = pct(normal[1], min, max) - bandLeft;
  const TrendIcon = trend === 'rising' ? ArrowUp : trend === 'falling' ? ArrowDown : null;

  return (
    <div className={cx('flex flex-col', cab ? 'gap-2' : 'gap-1.5', className)}>
      <div className="flex items-center justify-between gap-3">
        <span className={cx('text-ink-2', cab ? 'text-cab-small' : 'text-office-small')}>{label}</span>
        <StatusBadge status={s} />
      </div>
      <div className="flex items-baseline gap-2">
        <span className={cx('reading font-medium', cab ? 'text-cab-h1' : 'text-office-h1')}>
          {value == null ? '—' : value.toFixed(decimals)}
        </span>
        <span className={cx('text-ink-2', cab ? 'text-cab-small' : 'text-office-small')}>{unit}</span>
        {TrendIcon && (
          <TrendIcon
            size={cab ? 28 : 18}
            strokeWidth={2.5}
            className="self-center text-ink-2"
            aria-label={trend ? t(`trend.${trend}`) : undefined}
            role="img"
          />
        )}
      </div>
      <div
        className={cx('relative w-full overflow-hidden rounded-sm bg-raised', cab ? 'h-4' : 'h-2.5')}
        role="meter"
        aria-label={label}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={value ?? undefined}
        aria-valuetext={
          value == null
            ? t('gauge.noReading')
            : t('gauge.valueText', { value: value.toFixed(decimals), unit, range: `${normal[0]}–${normal[1]}`, status: t(`status.${s}`) })
        }
      >
        {value != null && (
          <div
            className={cx('absolute inset-y-0 left-0 rounded-sm transition-state', meta.bg)}
            style={{ width: `${pct(value, min, max)}%` }}
          />
        )}
        <div className="absolute inset-y-0 bg-band" style={{ left: `${bandLeft}%`, width: `${bandWidth}%` }} />
      </div>
      <div className={cx('flex justify-between text-ink-2 reading', cab ? 'text-cab-small' : 'text-office-small')}>
        <span>{min}</span>
        <span>{t('gauge.normal', { range: `${normal[0]}–${normal[1]}` })}</span>
        <span>{max}</span>
      </div>
    </div>
  );
}
