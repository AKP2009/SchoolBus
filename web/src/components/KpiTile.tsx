import { ArrowDown, ArrowUp } from 'lucide-react';
import { cx } from '@/lib/cx';
import { useMode } from '@/lib/mode';
import type { Status } from '@/lib/status';
import { StatusBadge } from './StatusBadge';

export interface KpiTileProps {
  label: string;
  value: string | number;
  unit?: string;
  /** Change versus the comparison period, e.g. { value: '+4.2', direction: 'up', caption: 'vs last week' }. */
  delta?: { value: string; direction: 'up' | 'down'; caption: string };
  /** Only when the KPI is itself a status (e.g. open critical alerts). Plain numbers stay neutral. */
  status?: Status;
  statusLabel?: string;
  className?: string;
}

export function KpiTile({ label, value, unit, delta, status, statusLabel, className }: KpiTileProps) {
  const mode = useMode();
  const cab = mode === 'cab';
  const DeltaIcon = delta?.direction === 'up' ? ArrowUp : ArrowDown;
  return (
    <div className={cx('flex flex-col rounded-md border bg-surface', cab ? 'gap-2 p-6' : 'gap-1 p-4', className)}>
      <div className="flex items-center justify-between gap-2">
        <span className={cx('text-ink-2', cab ? 'text-cab-small' : 'text-office-small')}>{label}</span>
        {status && <StatusBadge status={status} label={statusLabel} />}
      </div>
      <p className="flex items-baseline gap-1">
        <span className={cx('reading', cab ? 'text-cab-display' : 'text-office-kpi')}>{value}</span>
        {unit && <span className={cx('text-ink-2', cab ? 'text-cab-body' : 'text-office-body')}>{unit}</span>}
      </p>
      {delta && (
        <p className={cx('flex items-center gap-1 text-ink-2', cab ? 'text-cab-small' : 'text-office-small')}>
          <DeltaIcon size={cab ? 20 : 14} aria-label={delta.direction === 'up' ? 'up' : 'down'} role="img" />
          <span className="reading text-ink">{delta.value}</span> {delta.caption}
        </p>
      )}
    </div>
  );
}
