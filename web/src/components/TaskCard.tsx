import { CircleCheck, Clock, Play } from 'lucide-react';
import { cx } from '@/lib/cx';
import { useMode } from '@/lib/mode';
import type { Task, TaskStatus } from '@/types/domain';
import { Button } from './Button';
import { StatusBadge } from './StatusBadge';

export interface TaskCardProps {
  task: Task;
  /** 1-based position in today's list. */
  position?: number;
  onStart?: (taskId: string) => void;
  onComplete?: (taskId: string) => void;
  /** Queued offline, not yet synced (design.md §Offline: a small clock icon). */
  pendingSync?: boolean;
  className?: string;
}

const TYPE_WORD: Record<Task['task_type'], string> = {
  dig: 'Dig',
  trench: 'Trench',
  load: 'Load',
  haul: 'Haul',
  grade: 'Grade',
  backfill: 'Backfill',
};

const UNIT_WORD: Record<Task['unit'], string> = { m3: 'm³', tons: 't' };

const STATUS_BADGE: Partial<Record<TaskStatus, { status: 'ok' | 'warning' | 'offline'; label: string }>> = {
  completed: { status: 'ok', label: 'Done' },
  delayed: { status: 'warning', label: 'Delayed' },
  cancelled: { status: 'offline', label: 'Cancelled' },
};

/** Title · quantity · predicted time with range · top factor chips · state button (design.md §Task card). */
export function TaskCard({ task, position, onStart, onComplete, pendingSync, className }: TaskCardProps) {
  const mode = useMode();
  const cab = mode === 'cab';
  const factors = [...(task.prediction_factors ?? [])]
    .sort((a, b) => Math.abs(b.impact_min) - Math.abs(a.impact_min))
    .slice(0, 2);
  const badge = STATUS_BADGE[task.status];
  const p50 = task.predicted_p50_min;

  return (
    <article
      className={cx(
        'flex flex-col rounded-md border bg-surface',
        task.status === 'in_progress' ? 'border-saffron-500' : 'border-line',
        cab ? 'gap-4 p-6' : 'gap-3 p-4',
        className,
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className={cab ? 'text-cab-h2' : 'text-office-h3'}>
            {position != null && <span className="reading mr-2 text-ink-2">{position}.</span>}
            {TYPE_WORD[task.task_type]} {task.material_type}
          </h3>
          <p className={cx('text-ink-2', cab ? 'text-cab-body' : 'text-office-body')}>
            <span className="reading">{task.quantity}</span> {UNIT_WORD[task.unit]}
            <span className={cx('reading ml-3', cab ? 'text-cab-small' : 'text-office-small')}>{task.task_id}</span>
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className={cx('reading font-medium', cab ? 'text-cab-display' : 'text-office-kpi')}>
            {p50 == null ? '—' : Math.round(p50)}
            <span className={cx('ml-1 font-sans font-normal text-ink-2', cab ? 'text-cab-body' : 'text-office-body')}>
              min
            </span>
          </p>
          {task.predicted_p10_min != null && task.predicted_p90_min != null && (
            <p className={cx('text-ink-2', cab ? 'text-cab-small' : 'text-office-small')}>
              usually{' '}
              <span className="reading">
                {Math.round(task.predicted_p10_min)}–{Math.round(task.predicted_p90_min)}
              </span>
            </p>
          )}
        </div>
      </div>

      {factors.length > 0 && (
        <ul className="flex flex-wrap gap-2" aria-label="Biggest factors in this estimate">
          {factors.map((f) => (
            <li
              key={f.feature}
              className={cx(
                'rounded-sm border bg-raised text-ink',
                cab ? 'px-3 py-1 text-cab-small' : 'px-2 py-0.5 text-office-small',
              )}
            >
              {f.label}{' '}
              <span className="reading">
                {f.impact_min >= 0 ? '+' : '−'}
                {Math.abs(Math.round(f.impact_min))} min
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center justify-between gap-4">
        {pendingSync ? (
          <span className={cx('flex items-center gap-2 text-ink-2', cab ? 'text-cab-small' : 'text-office-small')}>
            <Clock size={cab ? 24 : 16} aria-hidden />
            Waiting to sync
          </span>
        ) : (
          <span />
        )}
        {task.status === 'scheduled' && (
          <Button icon={Play} onClick={() => onStart?.(task.task_id)}>
            Start task
          </Button>
        )}
        {task.status === 'in_progress' && (
          <Button icon={CircleCheck} onClick={() => onComplete?.(task.task_id)}>
            Complete task
          </Button>
        )}
        {badge && <StatusBadge status={badge.status} label={badge.label} />}
      </div>
    </article>
  );
}
