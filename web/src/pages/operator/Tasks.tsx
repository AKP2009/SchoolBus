import { useState } from 'react';
import { CalendarClock, Check, ClipboardList, Hourglass, Play, X } from 'lucide-react';
import { Button } from '@/components/Button';
import { DataState } from '@/components/DataState';
import { StatusBadge } from '@/components/StatusBadge';
import { TaskCard } from '@/components/TaskCard';
import { acceptPlan, updateTask, usePlan, useTaskPredictions, useTasks, type TaskPatch } from '@/data/hooks';
import { cx } from '@/lib/cx';
import { fmtTime, minutes, TASK_WORD } from '@/lib/format';
import { useConnection } from '@/stores/connection';
import type { PlanResult, TaskRow } from '@/types/domain';
import { OperatorShell, useCab, useNow } from './Shell';

const DELAY_REASONS = ['Waiting for a truck', 'Rain', 'Machine problem', 'Refuelling', 'Area blocked', 'Break'];

export function OperatorTasks() {
  const cab = useCab();
  const res = useTasks(cab?.shiftId);
  return (
    <OperatorShell>
      <DataState
        res={res}
        errorTitle="Couldn't load tasks."
        empty={{ icon: ClipboardList, title: 'No tasks yet.', hint: "Your supervisor will assign today's work." }}
      >
        {(tasks) => <TaskList tasks={tasks} />}
      </DataState>
    </OperatorShell>
  );
}

function TaskList({ tasks }: { tasks: TaskRow[] }) {
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

  const open = tasks.filter((t) => t.status !== 'completed' && t.status !== 'cancelled');
  const current = open.find((t) => t.status === 'in_progress' || t.status === 'delayed') ?? open[0];
  const next = open.filter((t) => t !== current);
  const done = tasks.filter((t) => t.status === 'completed').length;
  const usual = preds.data?.find((p) => p.task_id === current?.task_id);

  const elapsed = current?.actual_start ? Math.max(0, (now - Date.parse(current.actual_start)) / 60000) : null;
  const left = elapsed != null && current?.predicted_p50_min != null ? current.predicted_p50_min - elapsed : null;

  if (!current) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-cab-h1">All tasks done</h1>
        <p className="text-cab-body text-ink-2">
          <span className="reading">{done}</span> of <span className="reading">{tasks.length}</span> tasks finished. Tell your supervisor you are free.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-cab-h1">Current task</h1>
          <p className="text-cab-small text-ink-2">
            <span className="reading">{done}</span> of <span className="reading">{tasks.length}</span> done
          </p>
        </div>
        {left != null && current.status === 'in_progress' && (
          <p className="text-right" aria-label={left >= 0 ? `About ${Math.round(left)} minutes left` : `${Math.round(-left)} minutes over the estimate`}>
            <span className={cx('reading text-cab-display', left < 0 && 'text-warning')}>{Math.abs(Math.round(left))}</span>
            <span className="ml-2 text-cab-body text-ink-2">{left >= 0 ? 'min left' : 'min over'}</span>
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
            Flag delay
          </Button>
        )}
        {current.status === 'delayed' && (
          <>
            <StatusBadge status="warning" label={current.delay_reason ? `Delayed: ${current.delay_reason}` : 'Delayed'} />
            <Button icon={Play} onClick={() => void set(current, { status: 'in_progress', delay_reason: null })}>
              Resume task
            </Button>
          </>
        )}
        {usual?.operator_avg_min != null && (
          <p className="text-cab-small text-ink-2">
            You usually take <span className="reading text-ink">{minutes(usual.operator_avg_min)}</span> for this
          </p>
        )}
      </div>

      {delayFor === current.task_id && (
        <section className="flex flex-col gap-3 rounded-md border bg-surface p-6" aria-label="Why is this task delayed?">
          <div className="flex items-center justify-between">
            <h2 className="text-cab-h2">Why is it delayed?</h2>
            <Button variant="ghost" icon={X} onClick={() => setDelayFor(null)}>
              Cancel
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
                {r}
              </Button>
            ))}
          </div>
        </section>
      )}

      {plan.data && plan.data.moved_to_next_shift.length > 0 && <PlanChange plan={plan.data} tasks={tasks} shiftId={cab.shiftId} />}

      {next.length > 0 && (
        <section className="flex flex-col gap-2" aria-label="Next tasks">
          <h2 className="text-cab-h2 text-ink-2">Next</h2>
          {next.map((t) => (
            <div key={t.task_id} className="flex items-center justify-between gap-4 rounded-md border bg-surface px-6 py-3">
              <p className="text-cab-body">
                <span className="reading mr-2 text-ink-2">{t.sequence_no}.</span>
                {TASK_WORD[t.task_type]} {t.material_type}
                <span className="reading ml-3 text-ink-2">
                  {t.quantity} {t.unit === 'm3' ? 'm³' : 't'}
                </span>
              </p>
              <p className="shrink-0 text-cab-body">
                <span className="reading">{t.predicted_p50_min == null ? '—' : Math.round(t.predicted_p50_min)}</span>
                <span className="text-cab-small text-ink-2"> min</span>
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
  const [hidden, setHidden] = useState(false);
  if (hidden) return null;
  const label = (id: string) => {
    const t = tasks.find((x) => x.task_id === id);
    return t ? `Task ${t.sequence_no} · ${TASK_WORD[t.task_type]} ${t.material_type}` : id;
  };
  return (
    <section className="flex flex-col gap-3 rounded-md border border-info bg-surface p-4" aria-label="Plan update">
      <p className="flex items-start gap-3 text-cab-body">
        <CalendarClock size={32} className="shrink-0 text-info" aria-hidden />
        <span>
          <span className="font-medium">Plan update. </span>
          {plan.explanation}
        </span>
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Button icon={Check} onClick={() => void acceptPlan(shiftId, plan)}>
          Accept new plan
        </Button>
        <Button variant="secondary" onClick={() => setHidden(true)}>
          Keep current plan
        </Button>
        <ul className="flex flex-wrap gap-2 text-cab-small">
          {plan.moved_to_next_shift.map((id) => (
            <li key={id} className="rounded-sm border bg-raised px-3 py-1">
              {label(id)} → next shift
            </li>
          ))}
          {plan.break && (
            <li className="rounded-sm border bg-raised px-3 py-1">
              Break <span className="reading">{fmtTime(plan.break.start)}</span>
            </li>
          )}
        </ul>
      </div>
    </section>
  );
}
