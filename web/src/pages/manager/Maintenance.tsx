import { Link } from 'react-router-dom';
import { HeartPulse } from 'lucide-react';
import { DataState } from '@/components/DataState';
import { StatusBadge } from '@/components/StatusBadge';
import { useFleet, useFleetHealth, useMaintenance } from '@/data/hooks';
import { COMPONENT_WORD, fmtDateTime, pct } from '@/lib/format';
import { healthStatus } from '@/lib/status';
import { riskStatus, riskWord } from './Fleet';
import { ManagerShell, useSiteId } from './Shell';

/** Maintenance risk board (features.md §6.4): machines ranked by failure probability. */
export function ManagerMaintenance() {
  const all = useMaintenance();
  const health = useFleetHealth();
  const fleet = useFleet(useSiteId());
  const onSite = new Set((fleet.data?.machines ?? []).map((m) => m.machine_id));
  const res = { ...all, status: fleet.status === 'loading' ? ('loading' as const) : all.status, data: all.data?.filter((r) => onSite.has(r.machine_id)) };
  const byHealth = new Map((health.data ?? []).map((h) => [h.machine_id, h]));

  return (
    <ManagerShell title="Maintenance risk">
      <p className="col-span-12 max-w-[75ch] text-office-body text-ink-2">
        Chance of a failure in the next 48 engine hours, from the predictive model, with the part most likely to fail and the signals behind it.
        Medium is 30% or more, high is 60% or more.
      </p>
      <section className="col-span-12" aria-label="Machines ranked by failure risk">
        <DataState
          res={res}
          errorTitle="Couldn't load failure risk."
          empty={{ icon: HeartPulse, title: 'No risk scores yet.', hint: 'Machines ranked by failure chance appear after the first model run.' }}
        >
          {(rows) => (
            <table className="w-full rounded-md border bg-surface text-office-body">
              <thead className="bg-raised text-left text-office-small text-ink-2">
                <tr>
                  <th className="px-3 py-2 font-normal">#</th>
                  <th className="px-3 py-2 font-normal">Machine</th>
                  <th className="px-3 py-2 text-right font-normal">Chance in 48 h</th>
                  <th className="px-3 py-2 font-normal">Risk</th>
                  <th className="px-3 py-2 font-normal">Likely part</th>
                  <th className="px-3 py-2 font-normal">Top reasons</th>
                  <th className="px-3 py-2 text-right font-normal">Health now</th>
                  <th className="px-3 py-2 font-normal">Scored</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const h = byHealth.get(r.machine_id);
                  return (
                    <tr key={r.machine_id} className="border-t align-top">
                      <td className="reading px-3 py-2 text-ink-2">{i + 1}</td>
                      <td className="px-3 py-2">
                        <Link to={`/manager/machines/${r.machine_id}`} className="reading underline">
                          {r.machine_id}
                        </Link>
                      </td>
                      <td className="reading px-3 py-2 text-right">{pct(r.failure_probability, r.failure_probability < 0.01 ? 1 : 0)}</td>
                      <td className="px-3 py-2">
                        <StatusBadge status={riskStatus(r.failure_probability)} label={riskWord(r.failure_probability)} />
                      </td>
                      <td className="px-3 py-2">{COMPONENT_WORD[r.likely_component ?? 'other']}</td>
                      <td className="max-w-[420px] px-3 py-2 text-office-small">
                        <ul className="flex flex-col gap-0.5">
                          {r.top_factors.map((f) => (
                            <li key={f.feature}>{f.label ?? f.feature}</li>
                          ))}
                        </ul>
                      </td>
                      <td className="px-3 py-2 text-right">
                        {h ? <StatusBadge status={healthStatus(h.overall)} label={pct(h.overall)} /> : <span className="text-ink-2">—</span>}
                      </td>
                      <td className="reading px-3 py-2 text-office-small text-ink-2">{fmtDateTime(r.predicted_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </DataState>
      </section>
    </ManagerShell>
  );
}
