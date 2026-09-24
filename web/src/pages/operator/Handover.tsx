import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, ClipboardList, History, Moon, Sun } from 'lucide-react';
import { Button } from '@/components/Button';
import { DataState } from '@/components/DataState';
import { StatusBadge } from '@/components/StatusBadge';
import { useHandover, useMachineLogs } from '@/data/hooks';
import { cx } from '@/lib/cx';
import { fmtDay, fmtTime, ISSUE_WORD, SAFETY_WORD, TASK_WORD } from '@/lib/format';
import type { SeverityLevel, MachineLogShift, SafetyEventType } from '@/types/domain';
import { useSession } from '@/stores/session';
import { OperatorShell, useCab } from './Shell';

const SEV_STATUS: Record<SeverityLevel, 'info' | 'warning' | 'critical' | 'emergency'> = {
  info: 'info',
  warning: 'warning',
  critical: 'critical',
  emergency: 'emergency',
};

// The five lines of models.md §9, in order.
const LINE_LABEL = ['Machine', 'Open issues', 'Check first', 'Unfinished work', 'Fuel'];

export function OperatorHandover() {
  const cab = useCab();
  const navigate = useNavigate();
  const res = useHandover(cab?.machineId, cab?.shiftStart);
  const [tab, setTab] = useState<'brief' | 'log'>('brief');

  const start = () => {
    useSession.getState().markHandoverSeen();
    navigate('/operator');
  };

  return (
    <OperatorShell>
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-cab-h1">Handover</h1>
        <div className="flex gap-2">
        <div className="flex gap-2" role="tablist" aria-label="Handover views">
          {(
            [
              ['brief', 'Brief', ClipboardList],
              ['log', '7 days', History],
            ] as const
          ).map(([k, label, Icon]) => (
            <Button key={k} role="tab" aria-selected={tab === k} variant={tab === k ? 'primary' : 'secondary'} icon={Icon} onClick={() => setTab(k)}>
              {label}
            </Button>
          ))}
        </div>
        <Button icon={ArrowRight} onClick={start}>
          Start shift
        </Button>
        </div>
      </div>

      {tab === 'brief' ? (
        <DataState
          res={res}
          errorTitle="Couldn't load the handover."
          empty={{ icon: ClipboardList, title: 'No previous shift on this machine.', hint: 'You are the first operator on it. Do the usual walk-around before starting.' }}
        >
          {(h) => {
            const lines = (h.handover_summary ?? h.handover_notes ?? '').split('\n').filter(Boolean);
            return (
              <>
                <p className="flex items-center gap-3 text-cab-body text-ink-2">
                  {h.shift_type === 'night' ? <Moon size={28} aria-hidden /> : <Sun size={28} aria-hidden />}
                  <span>
                    {h.operator_name ?? h.operator_id} · {h.shift_type} shift{' '}
                    <span className="reading">
                      {fmtTime(h.start_time)}–{fmtTime(h.end_time)}
                    </span>
                  </span>
                </p>
                <ol className="flex flex-col divide-y divide-line rounded-md border bg-surface">
                  {lines.map((line, i) => (
                    <li key={i} className="grid grid-cols-[180px_1fr] gap-4 px-6 py-4">
                      <span className="text-cab-small text-ink-2">{LINE_LABEL[i] ?? 'Note'}</span>
                      <span className="text-cab-body">{line}</span>
                    </li>
                  ))}
                </ol>
                {h.issues_reported.length > 0 && (
                  <ul className="flex flex-wrap gap-2" aria-label="Issues reported">
                    {h.issues_reported.map((i) => (
                      <li key={i} className="rounded-sm border bg-raised px-3 py-1 text-cab-small">
                        {ISSUE_WORD[i] ?? i.replace(/_/g, ' ')}
                      </li>
                    ))}
                  </ul>
                )}
                {h.unfinished_tasks.length > 0 && (
                  <section className="flex flex-col gap-2">
                    <h2 className="text-cab-h2">Carried over</h2>
                    {h.unfinished_tasks.map((t) => (
                      <p key={t.task_id} className="flex items-center justify-between rounded-md border bg-surface px-6 py-3 text-cab-body">
                        <span>
                          Task <span className="reading">{t.sequence_no}</span> · {TASK_WORD[t.task_type]} {t.material_type}
                        </span>
                        <span className="reading text-ink-2">
                          {t.quantity} {t.unit === 'm3' ? 'm³' : 't'}
                        </span>
                      </p>
                    ))}
                  </section>
                )}
              </>
            );
          }}
        </DataState>
      ) : (
        <MachineLog />
      )}
    </OperatorShell>
  );
}

function MachineLog() {
  const cab = useCab();
  const res = useMachineLogs(cab?.machineId);
  return (
    <DataState
      res={res}
      isEmpty={(d) => !d || d.shifts.length === 0}
      errorTitle="Couldn't load the machine log."
      empty={{ icon: History, title: 'No shifts on this machine in the last 7 days.', hint: 'Shifts, alerts and incidents appear here as they happen.' }}
    >
      {(logs) => (
        <ol className="flex flex-col gap-3" aria-label="Last 7 days on this machine">
          {logs.shifts.map((s) => (
            <LogRow key={s.shift_id} s={s} />
          ))}
        </ol>
      )}
    </DataState>
  );
}

function LogRow({ s }: { s: MachineLogShift }) {
  const events = Object.entries(s.safety_events) as Array<[SafetyEventType, number]>;
  const worst = s.alerts.reduce<SeverityLevel | null>((w, a) => (w === 'critical' || a.severity === w ? w : a.severity === 'critical' ? 'critical' : w ?? a.severity), null);
  return (
    <li className={cx('flex flex-col gap-2 rounded-md border bg-surface px-6 py-4', s.in_progress && 'border-saffron-500')}>
      <div className="flex items-center justify-between gap-4">
        <p className="text-cab-body">
          <span className="reading">{fmtDay(s.start_time)}</span> · {s.shift_type} · {s.operator_name ?? s.operator_id}
          {s.in_progress && <span className="text-ink-2"> · now</span>}
        </p>
        <span className="reading text-cab-small text-ink-2">
          {s.tasks_completed}/{s.tasks_total} tasks
        </span>
      </div>
      {s.handover_notes && <p className="text-cab-small text-ink-2">{s.handover_notes}</p>}
      {(s.alerts.length > 0 || events.length > 0 || s.incidents.length > 0) && (
        <div className="flex flex-wrap gap-2">
          {worst && <StatusBadge status={SEV_STATUS[worst]} label={`${s.alerts.length} alert${s.alerts.length === 1 ? '' : 's'}`} />}
          {events.map(([k, n]) => (
            <span key={k} className="rounded-sm border bg-raised px-3 py-1 text-cab-small">
              {SAFETY_WORD[k]} <span className="reading">×{n}</span>
            </span>
          ))}
          {s.incidents.map((i) => (
            <StatusBadge key={i.id} status="warning" label="Incident" />
          ))}
        </div>
      )}
    </li>
  );
}
