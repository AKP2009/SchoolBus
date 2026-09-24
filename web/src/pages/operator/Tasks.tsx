import { useState } from 'react';
import { CalendarClock, Check, ClipboardList, Hourglass, Play, X } from 'lucide-react';
import { Button } from '@/components/Button';
import { DataState } from '@/components/DataState';
import { StatusBadge } from '@/components/StatusBadge';
import { TaskCard } from '@/components/TaskCard';
import { acceptPlan, updateTask, usePlan, useTaskPredictions, useTasks, type TaskPatch } from '@/data/hooks';
import { cx } from '@/lib/cx';
import { fmtTime, minutes } from '@/lib/format';
import { useConnection } from '@/stores/connection';
import type { PlanResult, TaskRow } from '@/types/domain';
import { OperatorShell, useCab, useNow } from './Shell';
import { useT, type T } from '@/i18n';

// Stored in English (tasks.delay_reason is read by the office); shown in the cab's language.
const DELAY_REASONS = ['Waiting for a truck', 'Rain', 'Machine problem', 'Refuelling', 'Area blocked', 'Break'];
const delayWord = (t: T, r: string) => t.dyn(`delay.${r}`, r);
const taskName = (t: T, task: Pick<TaskRow, 'task_type' | 'material_type'>) =>
  `${t(`taskType.${task.task_type}`)} ${t(`material.${task.material_type}`)}`;

export function OperatorTasks() {
  const t = useT();
  const cab = useCab();
  const res = useTasks(cab?.shiftId);
  return (
    <OperatorShell>
      <DataState
        res={res}
        errorTitle={t('tasks.loadError')}
        empty={{ icon: ClipboardList, title: t('tasks.empty'), hint: t('tasks.emptyHint') }}
      >
        {(tasks) => <TaskList tasks={tasks} />}
      </DataState>
    </OperatorShell>
  );
}

function TaskList({ tasks }: { tasks: TaskRow[] }) {
  const t = useT();
  const cab = useCab()!;
  const now = useNow();
  const online = useConnection((s) => s.online);
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [delayFor, setDelayFor] = useState<string | null>(null);
  const preds = useTaskPredictions(tasks.map((t) => t.task_id));
  const plan = usePlan(cab.shiftId);

  const set = async (task: TaskRow, patch: TaskPatch) => {
    const { queued } = await updateTask(cab.shiftId, task.task_id, patch);
    if (queued) setPending((p) => new Set(p).add(task.task_id));
  };
  const nowIso = () => new Date(now).toISOString();

  const open = tasks.filter((x) => x.status !== 'completed' && x.status !== 'cancelled');
  const current = open.find((x) => x.status === 'in_progress' || x.status === 'delayed') ?? open[0];
  const next = open.filter((x) => x !== current);
  const done = tasks.filter((x) => x.status === 'completed').length;
  const usual = preds.data?.find((p) => p.task_id === current?.task_id);

  const elapsed = current?.actual_start ? Math.max(0, (now - Date.parse(current.actual_start)) / 60000) : null;
  const left = elapsed != null && current?.predicted_p50_min != null ? current.predicted_p50_min - elapsed : null;

  if (!current) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-cab-h1">{t('tasks.allDone')}</h1>
        <p className="text-cab-body text-ink-2">
          {t.rich('tasks.allDoneText', { done: <span className="reading">{done}</span>, total: <span className="reading">{tasks.length}</span> })}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-cab-h1">{t('tasks.current')}</h1>
          <p className="text-cab-small text-ink-2">
            {t.rich('tasks.doneOf', { done: <span className="reading">{done}</span>, total: <span className="reading">{tasks.length}</span> })}
          </p>
        </div>
        {left != null && current.status === 'in_progress' && (
          <p className="text-right" aria-label={left >= 0 ? t('tasks.minLeftAria', { n: Math.round(left) }) : t('tasks.minOverAria', { n: Math.round(-left) })}>
            <span className={cx('reading text-cab-display', left < 0 && 'text-warning')}>{Math.abs(Math.round(left))}</span>
            <span className="ml-2 text-cab-body text-ink-2">{left >= 0 ? t('tasks.minLeft') : t('tasks.minOver')}</span>
          </p>
        )}
      </div>

      <TaskCard
        task={current}
        position={current.sequence_no}
        pendingSync={pending.has(current.task_id) && !online}
        onStart={() => void set(current, { status: 'in_progress', actual_start: nowIso() })}
        onComplete={() => void set(current, { status: 'completed', actual_end: nowIso() })}
      />

      <div className="flex flex-wrap items-center gap-4">
        {current.status === 'in_progress' && (
          <Button variant="secondary" icon={Hourglass} onClick={() => setDelayFor(current.task_id)}>
            {t('tasks.flagDelay')}
          </Button>
        )}
        {current.status === 'delayed' && (
          <>
            <StatusBadge status="warning" label={current.delay_reason ? t('tasks.delayedBecause', { reason: delayWord(t, current.delay_reason) }) : t('task.delayed')} />
            <Button icon={Play} onClick={() => void set(current, { status: 'in_progress', delay_reason: null })}>
              {t('tasks.resume')}
            </Button>
          </>
        )}
        {usual?.operator_avg_min != null && (
          <p className="text-cab-small text-ink-2">
            {t.rich('tasks.usually', { time: <span className="reading text-ink">{minutes(usual.operator_avg_min)}</span> })}
          </p>
        )}
      </div>

      {delayFor === current.task_id && (
        <section className="flex flex-col gap-3 rounded-md border bg-surface p-6" aria-label={t('tasks.whyDelayedAria')}>
          <div className="flex items-center justify-between">
            <h2 className="text-cab-h2">{t('tasks.whyDelayed')}</h2>
            <Button variant="ghost" icon={X} onClick={() => setDelayFor(null)}>
              {t('tasks.cancel')}
            </Button>
          </div>
          <div className="grid grid-cols-3 gap-4">
            {DELAY_REASONS.map((r) => (
              <Button
                key={r}
                variant="secondary"
                onClick={() => {
                  void set(current, { status: 'delayed', delay_reason: r });
                  setDelayFor(null);
                }}
              >
                {delayWord(t, r)}
              </Button>
            ))}
          </div>
        </section>
      )}

      {plan.data && plan.data.moved_to_next_shift.length > 0 && <PlanChange plan={plan.data} tasks={tasks} shiftId={cab.shiftId} />}

      {next.length > 0 && (
        <section className="flex flex-col gap-2" aria-label={t('tasks.nextAria')}>
          <h2 className="text-cab-h2 text-ink-2">{t('tasks.next')}</h2>
          {next.map((n) => (
            <div key={n.task_id} className="flex items-center justify-between gap-4 rounded-md border bg-surface px-6 py-3">
              <p className="text-cab-body">
                <span className="reading mr-2 text-ink-2">{n.sequence_no}.</span>
                {taskName(t, n)}
                <span className="reading ml-3 text-ink-2">
                  {n.quantity} {n.unit === 'm3' ? t('unit.m3') : t('unit.t')}
                </span>
              </p>
              <p className="shrink-0 text-cab-body">
                <span className="reading">{n.predicted_p50_min == null ? '—' : Math.round(n.predicted_p50_min)}</span>
                <span className="text-cab-small text-ink-2"> {t('unit.min')}</span>
              </p>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

/** Adaptive mission control (features.md §1.4): what changed and why; the operator accepts. */
function PlanChange({ plan, tasks, shiftId }: { plan: PlanResult; tasks: TaskRow[]; shiftId: string }) {
  const t = useT();
  const [hidden, setHidden] = useState(false);
  if (hidden) return null;
  const label = (id: string) => {
    const x = tasks.find((y) => y.task_id === id);
    return x ? t('plan.task', { n: x.sequence_no, type: t(`taskType.${x.task_type}`), material: t(`material.${x.material_type}`) }) : id;
  };
  return (
    <section className="flex flex-col gap-3 rounded-md border border-info bg-surface p-4" aria-label={t('plan.aria')}>
      <p className="flex items-start gap-3 text-cab-body">
        <CalendarClock size={32} className="shrink-0 text-info" aria-hidden />
        <span>
          <span className="font-medium">{t('plan.update')} </span>
          {plan.explanation}
        </span>
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Button icon={Check} onClick={() => void acceptPlan(shiftId, plan)}>
          {t('plan.accept')}
        </Button>
        <Button variant="secondary" onClick={() => setHidden(true)}>
          {t('plan.keep')}
        </Button>
        <ul className="flex flex-wrap gap-2 text-cab-small">
          {plan.moved_to_next_shift.map((id) => (
            <li key={id} className="rounded-sm border bg-raised px-3 py-1">
              {t('plan.toNextShift', { task: label(id) })}
            </li>
          ))}
          {plan.break && (
            <li className="rounded-sm border bg-raised px-3 py-1">
              {t.rich('plan.break', { time: <span className="reading">{fmtTime(plan.break.start)}</span> })}
            </li>
          )}
        </ul>
      </div>
    </section>
  );
}
