import { Link, useNavigate } from 'react-router-dom';
import { HeartPulse, Map as MapIcon, Trophy } from 'lucide-react';
import { DataState } from '@/components/DataState';
import { FleetMap, machineStatus } from '@/components/FleetMap';
import { KpiTile } from '@/components/KpiTile';
import { StatusBadge } from '@/components/StatusBadge';
import { useClusters, useFleet, useMaintenance, useOpenAlerts } from '@/data/hooks';
import { COMPONENT_WORD, pct } from '@/lib/format';
import { AlertsFeed } from './AlertsFeed';
import { ManagerShell, useSiteId } from './Shell';

export function riskStatus(p: number) {
  return p >= 0.6 ? ('critical' as const) : p >= 0.3 ? ('warning' as const) : ('ok' as const);
}
export function riskWord(p: number) {
  return p >= 0.6 ? 'High' : p >= 0.3 ? 'Medium' : 'Low';
}

export function ManagerFleet() {
  const siteId = useSiteId();
  const fleet = useFleet(siteId);
  const alerts = useOpenAlerts(siteId);
  const navigate = useNavigate();
  const machines = fleet.data?.machines ?? [];
  const active = machines.filter((m) => m.live.on_shift).length;
  const critical = (alerts.data ?? []).filter((a) => a.severity === 'critical' || a.severity === 'emergency').length;
  const withHealth = machines.filter((m) => m.health_overall != null);
  const avgHealth = withHealth.length ? withHealth.reduce((s, m) => s + m.health_overall!, 0) / withHealth.length : null;

  return (
    <ManagerShell title="Fleet">
      <KpiTile className="col-span-3" label="Machines on shift" value={fleet.data ? active : '—'} unit={fleet.data ? `/ ${machines.length}` : undefined} />
      <KpiTile className="col-span-3" label="Open alerts" value={alerts.data?.length ?? '—'} />
      <KpiTile className="col-span-3" label="Critical alerts open" value={alerts.data ? critical : '—'} status={!alerts.data ? undefined : critical > 0 ? 'critical' : 'ok'} statusLabel={critical > 0 ? 'Act now' : 'None'} />
      <KpiTile className="col-span-3" label="Average machine health" value={avgHealth == null ? '—' : Math.round(avgHealth * 100)} unit="%" />

      <section className="col-span-8 flex flex-col gap-3" aria-label="Fleet map">
        <DataState
          res={fleet}
          isEmpty={(f) => !f || f.machines.length === 0}
          errorTitle="Couldn't load the fleet map."
          empty={{ icon: MapIcon, title: 'No machines on this site yet.', hint: 'Machines appear here, coloured by status, once telemetry replay starts.' }}
          skeleton={<div className="h-[460px] rounded-md bg-raised" />}
        >
          {(f) => (
            <>
              <FleetMap machines={f.machines} geofences={f.geofences.filter((g) => g.active)} onSelect={(id) => navigate(`/manager/machines/${id}`)} className="h-[460px]" />
              <ul className="flex flex-wrap gap-2" aria-label="Machines">
                {f.machines.map((m) => (
                  <li key={m.machine_id}>
                    <Link to={`/manager/machines/${m.machine_id}`} className="flex min-h-touch-office items-center gap-2 rounded-md border bg-surface px-3 hover:bg-raised">
                      <span className="reading">{m.machine_id}</span>
                      <StatusBadge status={machineStatus(m)} label={m.live.on_shift ? undefined : 'Off shift'} />
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          )}
        </DataState>
      </section>

      <section className="col-span-4 flex max-h-[560px] min-h-0 flex-col gap-3 overflow-auto" aria-label="Live alerts">
        <h2 className="text-office-h2">Live alerts</h2>
        <AlertsFeed compact />
      </section>

      <RiskPreview />
      <RankingPreview />
    </ManagerShell>
  );
}

function RiskPreview() {
  const all = useMaintenance();
  const fleet = useFleet(useSiteId());
  const onSite = new Set((fleet.data?.machines ?? []).map((m) => m.machine_id));
  const res = { ...all, data: all.data?.filter((r) => onSite.has(r.machine_id)) };
  return (
    <section className="col-span-6 flex flex-col gap-3 rounded-md border bg-surface p-4" aria-label="Maintenance risk">
      <div className="flex items-center justify-between">
        <h2 className="text-office-h2">Maintenance risk</h2>
        <Link to="/manager/health" className="text-office-body underline">
          All machines
        </Link>
      </div>
      <DataState res={res} errorTitle="Couldn't load failure risk." empty={{ icon: HeartPulse, title: 'No risk scores yet.', hint: 'Machines ranked by failure chance appear after the first model run.' }}>
        {(rows) => (
          <table className="w-full text-office-body">
            <thead className="text-left text-office-small text-ink-2">
              <tr>
                <th className="py-1 font-normal">Machine</th>
                <th className="py-1 font-normal">Likely part</th>
                <th className="py-1 text-right font-normal">Chance in 48 h</th>
                <th className="py-1 text-right font-normal">Risk</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 5).map((r) => (
                <tr key={r.machine_id} className="border-t">
                  <td className="py-2">
                    <Link to={`/manager/machines/${r.machine_id}`} className="reading underline">
                      {r.machine_id}
                    </Link>
                  </td>
                  <td className="py-2">{COMPONENT_WORD[r.likely_component ?? 'other']}</td>
                  <td className="reading py-2 text-right">{pct(r.failure_probability, r.failure_probability < 0.01 ? 1 : 0)}</td>
                  <td className="py-2 text-right">
                    <StatusBadge status={riskStatus(r.failure_probability)} label={riskWord(r.failure_probability)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </DataState>
    </section>
  );
}

function RankingPreview() {
  const res = useClusters();
  return (
    <section className="col-span-6 flex flex-col gap-3 rounded-md border bg-surface p-4" aria-label="Efficiency ranking">
      <div className="flex items-center justify-between">
        <h2 className="text-office-h2">Operator efficiency</h2>
        <Link to="/manager/clusters" className="text-office-body underline">
          Clusters
        </Link>
      </div>
      <DataState
        res={res}
        isEmpty={(c) => !c || c.metrics.length === 0}
        errorTitle="Couldn't load the ranking."
        empty={{ icon: Trophy, title: 'No ranking yet.', hint: 'Operators and machines ranked by weekly metrics appear here.' }}
      >
        {(c) => (
          <table className="w-full text-office-body">
            <thead className="text-left text-office-small text-ink-2">
              <tr>
                <th className="py-1 font-normal">Rank</th>
                <th className="py-1 font-normal">Operator</th>
                <th className="py-1 font-normal">Group</th>
                <th className="py-1 text-right font-normal">Idle</th>
              </tr>
            </thead>
            <tbody>
              {c.metrics
                .filter((m) => m.entity_type === 'operator')
                .sort((a, b) => (a.rank_in_site ?? 99) - (b.rank_in_site ?? 99))
                .slice(0, 5)
                .map((m) => (
                  <tr key={m.id} className="border-t">
                    <td className="reading py-2">{m.rank_in_site}</td>
                    <td className="py-2">{c.names[m.entity_id] ?? m.entity_id}</td>
                    <td className="py-2">{m.cluster_label}</td>
                    <td className="reading py-2 text-right">{m.idle_pct?.toFixed(0)}%</td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
      </DataState>
      {res.data && <p className="text-office-small text-ink-2">Week of {res.data.week_start}</p>}
    </section>
  );
}
