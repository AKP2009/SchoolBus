import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Bell, Check, CheckCheck } from 'lucide-react';
import { Button } from '@/components/Button';
import { DataState } from '@/components/DataState';
import { StatusBadge } from '@/components/StatusBadge';
import { acknowledgeAlert, resolveAlert, useOpenAlerts } from '@/data/hooks';
import { cx } from '@/lib/cx';
import { ago, fmtTime } from '@/lib/format';
import type { AlertRow, AlertStage, SeverityLevel } from '@/types/domain';
import { useOfficeNow, useSiteId } from './Shell';

export const STAGE_WORD: Record<AlertStage, string> = {
  warn: 'Operator warned',
  derate: 'Power reduced',
  recommend_shutdown: 'Safe shutdown advised',
  escalated: 'Escalated to you',
  resolved: 'Resolved',
};

const FILTERS: Array<{ key: 'all' | SeverityLevel; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'critical', label: 'Critical' },
  { key: 'warning', label: 'Warning' },
  { key: 'info', label: 'Note' },
];

function matches(a: AlertRow, f: 'all' | SeverityLevel) {
  if (f === 'all') return true;
  if (f === 'critical') return a.severity === 'critical' || a.severity === 'emergency';
  return a.severity === f;
}

export function AlertsFeed({ compact = false, machineId }: { compact?: boolean; machineId?: string }) {
  const siteId = useSiteId();
  const res = useOpenAlerts(siteId);
  const now = useOfficeNow();
  const [filter, setFilter] = useState<'all' | SeverityLevel>('all');

  return (
    <div className="flex min-h-0 flex-col gap-3">
      {!compact && (
        <div className="flex gap-2" role="group" aria-label="Filter by severity">
          {FILTERS.map((f) => (
            <Button key={f.key} variant={filter === f.key ? 'primary' : 'secondary'} aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>
              {f.label}
              {res.data && <span className="reading ml-2">{res.data.filter((a) => matches(a, f.key)).length}</span>}
            </Button>
          ))}
        </div>
      )}
      <DataState
        res={res}
        isEmpty={(rows) => rows.filter((a) => (!machineId || a.machine_id === machineId) && matches(a, filter)).length === 0}
        errorTitle="Couldn't load alerts."
        empty={{ icon: Bell, title: 'No open alerts.', hint: 'New alerts from any machine on this site appear here as they happen.' }}
        skeleton={
          <>
            <div className="h-16 rounded-md bg-raised" />
            <div className="h-16 rounded-md bg-raised" />
          </>
        }
      >
        {(rows) => (
          <ul className="flex flex-col gap-2" aria-live="polite" aria-label="Open alerts">
            {rows
              .filter((a) => (!machineId || a.machine_id === machineId) && matches(a, filter))
              .map((a) => (
                <AlertItem key={a.id} a={a} now={now} compact={compact} />
              ))}
          </ul>
        )}
      </DataState>
    </div>
  );
}

function AlertItem({ a, now, compact }: { a: AlertRow; now: number; compact: boolean }) {
  const escalated = a.stage === 'escalated';
  return (
    <li className={cx('flex flex-col gap-2 rounded-md border bg-surface p-3', escalated && 'border-critical', a.severity === 'emergency' && 'anim-emergency')}>
      <div className="flex items-start justify-between gap-3">
        <StatusBadge status={a.severity} />
        <span className="text-office-small text-ink-2" title={fmtTime(a.ts)}>
          {ago(a.ts, now)}
        </span>
      </div>
      <p className="text-office-h3">{a.title}</p>
      <p className="text-office-small text-ink-2">
        {a.machine_id && (
          <Link to={`/manager/machines/${a.machine_id}`} className="reading text-ink underline">
            {a.machine_id}
          </Link>
        )}
        {' · '}
        {STAGE_WORD[a.stage]}
        {a.acknowledged_at && ' · acknowledged'}
      </p>
      {!compact && a.recommended_action && <p className="text-office-body">{a.recommended_action}</p>}
      <div className="flex gap-2">
        {!a.acknowledged_at && (
          <Button variant="secondary" icon={Check} onClick={() => void acknowledgeAlert(a.id)}>
            Acknowledge
          </Button>
        )}
        <Button variant="ghost" icon={CheckCheck} onClick={() => void resolveAlert(a.id)}>
          Resolve
        </Button>
      </div>
    </li>
  );
}
