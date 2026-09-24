import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, ClipboardList, History, Moon, Sun } from 'lucide-react';
import { Button } from '@/components/Button';
import { DataState } from '@/components/DataState';
import { StatusBadge } from '@/components/StatusBadge';
import { useHandover, useMachineLogs } from '@/data/hooks';
import { cx } from '@/lib/cx';
import { fmtDay, fmtTime } from '@/lib/format';
import type { SeverityLevel, MachineLogShift, SafetyEventType } from '@/types/domain';
import { useSession } from '@/stores/session';
import { OperatorShell, useCab } from './Shell';
import { useT } from '@/i18n';

const SEV_STATUS: Record<SeverityLevel, 'info' | 'warning' | 'critical' | 'emergency'> = {
  info: 'info',
  warning: 'warning',
  critical: 'critical',
  emergency: 'emergency',
};

// The five lines of models.md §9, in order (handover.line.0 … 4).

export function OperatorHandover() {
  const t = useT();
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
        <h1 className="text-cab-h1">{t('handover.title')}</h1>
        <div className="flex gap-2">
        <div className="flex gap-2" role="tablist" aria-label={t('handover.viewsAria')}>
          {(
            [
              ['brief', t('handover.brief'), ClipboardList],
              ['log', t('handover.log'), History],
            ] as const
          ).map(([k, label, Icon]) => (
            <Button key={k} role="tab" aria-selected={tab === k} variant={tab === k ? 'primary' : 'secondary'} icon={Icon} onClick={() => setTab(k)}>
              {label}
            </Button>
          ))}
        </div>
        <Button icon={ArrowRight} onClick={start}>
          {t('handover.start')}
        </Button>
        </div>
      </div>

      {tab === 'brief' ? (
        <DataState
          res={res}
          errorTitle={t('handover.loadError')}
          empty={{ icon: ClipboardList, title: t('handover.empty'), hint: t('handover.emptyHint') }}
        >
          {(h) => {
            const lines = (h.handover_summary ?? h.handover_notes ?? '').split('\n').filter(Boolean);
            return (
              <>
                <p className="flex items-center gap-3 text-cab-body text-ink-2">
                  {h.shift_type === 'night' ? <Moon size={28} aria-hidden /> : <Sun size={28} aria-hidden />}
                  <span>
                    {t.rich('handover.shiftLine', {
                      name: h.operator_name ?? h.operator_id,
                      type: t(`shiftType.${h.shift_type}`),
                      time: (
                        <span className="reading">
                          {fmtTime(h.start_time)}–{fmtTime(h.end_time)}
                        </span>
                      ),
                    })}
                  </span>
                </p>
                <ol className="flex flex-col divide-y divide-line rounded-md border bg-surface">
                  {lines.map((line, i) => (
                    <li key={i} className="grid grid-cols-[180px_1fr] gap-4 px-6 py-4">
                      <span className="text-cab-small text-ink-2">{t.dyn(`handover.line.${i}`, t('handover.line.note'))}</span>
                      <span className="text-cab-body">{line}</span>
                    </li>
                  ))}
                </ol>
                {h.issues_reported.length > 0 && (
                  <ul className="flex flex-wrap gap-2" aria-label={t('handover.issuesAria')}>
                    {h.issues_reported.map((i) => (
                      <li key={i} className="rounded-sm border bg-raised px-3 py-1 text-cab-small">
                        {t.dyn(`issue.${i}`, i.replace(/_/g, ' '))}
                      </li>
                    ))}
                  </ul>
                )}
                {h.unfinished_tasks.length > 0 && (
                  <section className="flex flex-col gap-2">
                    <h2 className="text-cab-h2">{t('handover.carriedOver')}</h2>
                    {h.unfinished_tasks.map((u) => (
                      <p key={u.task_id} className="flex items-center justify-between rounded-md border bg-surface px-6 py-3 text-cab-body">
                        <span>
                          {t.rich('handover.task', {
                            n: <span className="reading">{u.sequence_no}</span>,
                            type: t(`taskType.${u.task_type}`),
                            material: t(`material.${u.material_type}`),
                          })}
                        </span>
                        <span className="reading text-ink-2">
                          {u.quantity} {u.unit === 'm3' ? t('unit.m3') : t('unit.t')}
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
  const t = useT();
  const cab = useCab();
  const res = useMachineLogs(cab?.machineId);
  return (
    <DataState
      res={res}
      isEmpty={(d) => !d || d.shifts.length === 0}
      errorTitle={t('log.loadError')}
      empty={{ icon: History, title: t('log.empty'), hint: t('log.emptyHint') }}
    >
      {(logs) => (
        <ol className="flex flex-col gap-3" aria-label={t('log.aria')}>
          {logs.shifts.map((s) => (
            <LogRow key={s.shift_id} s={s} />
          ))}
        </ol>
      )}
    </DataState>
  );
}

function LogRow({ s }: { s: MachineLogShift }) {
  const t = useT();
  const events = Object.entries(s.safety_events) as Array<[SafetyEventType, number]>;
  const worst = s.alerts.reduce<SeverityLevel | null>((w, a) => (w === 'critical' || a.severity === w ? w : a.severity === 'critical' ? 'critical' : w ?? a.severity), null);
  return (
    <li className={cx('flex flex-col gap-2 rounded-md border bg-surface px-6 py-4', s.in_progress && 'border-saffron-500')}>
      <div className="flex items-center justify-between gap-4">
        <p className="text-cab-body">
          <span className="reading">{fmtDay(s.start_time)}</span> · {t(`shiftType.${s.shift_type}`)} · {s.operator_name ?? s.operator_id}
          {s.in_progress && <span className="text-ink-2"> · {t('log.now')}</span>}
        </p>
        <span className="reading text-cab-small text-ink-2">
          {t('log.tasks', { done: s.tasks_completed, total: s.tasks_total })}
        </span>
      </div>
      {s.handover_notes && <p className="text-cab-small text-ink-2">{s.handover_notes}</p>}
      {(s.alerts.length > 0 || events.length > 0 || s.incidents.length > 0) && (
        <div className="flex flex-wrap gap-2">
          {worst && <StatusBadge status={SEV_STATUS[worst]} label={s.alerts.length === 1 ? t('log.alertOne') : t('log.alerts', { n: s.alerts.length })} />}
          {events.map(([k, n]) => (
            <span key={k} className="rounded-sm border bg-raised px-3 py-1 text-cab-small">
              {t(`event.${k}`)} <span className="reading">×{n}</span>
            </span>
          ))}
          {s.incidents.map((i) => (
            <StatusBadge key={i.id} status="warning" label={t('log.incident')} />
          ))}
        </div>
      )}
    </li>
  );
}
