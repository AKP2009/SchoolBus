import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, History, Truck } from 'lucide-react';
import { useShallow } from 'zustand/react/shallow';
import { DataState } from '@/components/DataState';
import { DigitalTwin } from '@/components/DigitalTwin';
import { FleetMap, machineStatus } from '@/components/FleetMap';
import { Gauge } from '@/components/Gauge';
import { KpiTile } from '@/components/KpiTile';
import { StatusBadge } from '@/components/StatusBadge';
import { useFleet, useMachineHealth, useMachineLogs, useMaintenance } from '@/data/hooks';
import { useLive } from '@/data/live';
import { COMPONENT_WORD, fmtDay, fmtTime, MACHINE_WORD, pct, SAFETY_WORD } from '@/lib/format';
import type { SafetyEventType, SeverityLevel } from '@/types/domain';
import { toScores } from '../operator/Machine';
import { AlertsFeed } from './AlertsFeed';
import { riskStatus, riskWord } from './Fleet';
import { ManagerShell, useSiteId } from './Shell';

export function ManagerMachine() {
  const { id = '' } = useParams();
  const siteId = useSiteId();
  const fleet = useFleet(siteId);
  const health = useMachineHealth(id);
  const maint = useMaintenance();
  const logs = useMachineLogs(id);
  const [liveMachine, telemetry, liveHealth] = useLive(useShallow((s) => [s.machineId, s.telemetry, s.health] as const));
  const m = fleet.data?.machines.find((x) => x.machine_id === id);
  const isLive = liveMachine === id && telemetry != null;
  const scores = toScores(isLive && liveHealth ? liveHealth : health.data);
  const risk = maint.data?.find((r) => r.machine_id === id);

  return (
    <ManagerShell
      title={`Machine ${id}`}
      actions={
        <Link to="/manager" className="flex min-h-touch-office items-center gap-2 text-office-body underline">
          <ArrowLeft size={18} aria-hidden /> Fleet
        </Link>
      }
    >
      <DataState
        res={fleet}
        isEmpty={(f) => !f?.machines.some((x) => x.machine_id === id)}
        errorTitle="Couldn't load this machine."
        empty={{ icon: Truck, title: `No machine ${id} on this site.`, hint: 'Pick a machine from the fleet map.' }}
        className="col-span-12"
        skeleton={<div className="col-span-12 h-24 rounded-md bg-raised" />}
      >
        {() =>
          m && (
            <>
              <div className="col-span-12 flex flex-wrap items-center gap-4 text-office-body">
                <span>
                  {m.model} {MACHINE_WORD[m.machine_type].toLowerCase()}
                </span>
                <StatusBadge status={machineStatus(m)} label={m.live.on_shift ? undefined : 'Off shift'} />
                {m.live.operator_name && <span className="text-ink-2">Operator: {m.live.operator_name}</span>}
                <span className="text-ink-2">
                  Service in <span className="reading text-ink">{Math.max(0, Math.round(m.service_interval_hours - m.hours_since_service))}</span> h
                </span>
              </div>
              <KpiTile className="col-span-3" label="Health" value={health.data || isLive ? Math.round(((isLive ? liveHealth?.overall : health.data?.overall) ?? 0) * 100) : '—'} unit="%" />
              <KpiTile
                className="col-span-3"
                label="Failure chance, next 48 h"
                value={risk ? pct(risk.failure_probability, risk.failure_probability < 0.01 ? 1 : 0).replace('%', '') : '—'}
                unit="%"
                status={risk ? riskStatus(risk.failure_probability) : undefined}
                statusLabel={risk ? riskWord(risk.failure_probability) : undefined}
              />
              <KpiTile className="col-span-3" label="Fuel" value={(isLive ? telemetry.fuel_level_pct : m.live.fuel_level_pct)?.toFixed(0) ?? '—'} unit="%" />
              <KpiTile className="col-span-3" label="Engine hours" value={m.total_engine_hours.toLocaleString('en-IN')} unit="h" />

              <section className="col-span-7 self-start rounded-md border bg-surface p-4" aria-label="Digital twin">
                <h2 className="mb-3 text-office-h2">Health by part</h2>
                {health.status === 'ready' || isLive ? (
                  <DigitalTwin scores={scores} machineType={m.machine_type} className="min-h-[260px]" />
                ) : (
                  <div className="h-[260px] rounded-md bg-raised" aria-busy="true" />
                )}
                {health.data?.reasons && health.data.reasons.length > 0 && (
                  <ul className="mt-3 flex flex-col gap-1 text-office-small text-ink-2">
                    {health.data.reasons.slice(0, 3).map((r) => (
                      <li key={r.text}>
                        {COMPONENT_WORD[r.subsystem]}: {r.source === 'rule' ? 'an open alert on this part' : r.text}
                      </li>
                    ))}
                  </ul>
                )}
              {isLive ? (
                <div className="mt-4 grid grid-cols-2 gap-6 border-t pt-4" aria-label="Live readings">
                  <Gauge label="Coolant" value={telemetry.coolant_temp_c} unit="°C" min={40} max={120} normal={[75, 100]} status={(telemetry.coolant_temp_c ?? 0) > 105 ? 'critical' : (telemetry.coolant_temp_c ?? 0) > 100 ? 'warning' : 'ok'} />
                  <Gauge label="Hydraulic oil" value={telemetry.hydraulic_oil_temp_c} unit="°C" min={20} max={110} normal={[40, 85]} status={(telemetry.hydraulic_oil_temp_c ?? 0) > 95 ? 'critical' : (telemetry.hydraulic_oil_temp_c ?? 0) > 90 ? 'warning' : 'ok'} />
                  <Gauge label="Oil pressure" value={telemetry.oil_pressure_kpa} unit="kPa" min={0} max={600} normal={[250, 500]} status="ok" />
                  <Gauge label="Engine load" value={telemetry.engine_load_pct} unit="%" min={0} max={100} normal={[0, 85]} status="ok" />
                </div>
              ) : null}
              </section>
              <section className="col-span-5 flex flex-col gap-3" aria-label="Location and alerts">
                <FleetMap machines={[m]} geofences={fleet.data?.geofences.filter((g) => g.active)} selectedId={m.machine_id} className="h-[220px]" />
                <h2 className="text-office-h2">Open alerts</h2>
                <AlertsFeed compact machineId={id} />
              </section>


              {risk && risk.top_factors.length > 0 && (
                <section className="col-span-12 rounded-md border bg-surface p-4" aria-label="Why this failure risk">
                  <h2 className="mb-2 text-office-h2">Why this risk ({COMPONENT_WORD[risk.likely_component ?? 'other']})</h2>
                  <ul className="flex flex-wrap gap-2 text-office-body">
                    {risk.top_factors.map((f) => (
                      <li key={f.feature} className="rounded-sm border bg-raised px-2 py-0.5">
                        {f.label ?? f.feature}
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              <section className="col-span-12 flex flex-col gap-3" aria-label="Last 7 days">
                <h2 className="text-office-h2">Last 7 days</h2>
                <DataState
                  res={logs}
                  isEmpty={(l) => !l || l.shifts.length === 0}
                  errorTitle="Couldn't load the machine log."
                  empty={{ icon: History, title: 'No log for this machine yet.', hint: 'Shifts, alerts and incidents appear here after the machine works a shift.' }}
                >
                  {(l) => (
                    <table className="w-full rounded-md border bg-surface text-office-body">
                      <thead className="bg-raised text-left text-office-small text-ink-2">
                        <tr>
                          <th className="px-3 py-2 font-normal">Shift</th>
                          <th className="px-3 py-2 font-normal">Operator</th>
                          <th className="px-3 py-2 text-right font-normal">Tasks</th>
                          <th className="px-3 py-2 font-normal">Alerts</th>
                          <th className="px-3 py-2 font-normal">Safety events</th>
                          <th className="px-3 py-2 font-normal">Handover note</th>
                        </tr>
                      </thead>
                      <tbody>
                        {l.shifts.map((s) => (
                          <tr key={s.shift_id} className="border-t align-top">
                            <td className="px-3 py-2">
                              <span className="reading">{fmtDay(s.start_time)}</span> {s.shift_type}
                              <br />
                              <span className="reading text-office-small text-ink-2">
                                {fmtTime(s.start_time)}–{fmtTime(s.end_time)}
                              </span>
                            </td>
                            <td className="px-3 py-2">{s.operator_name ?? s.operator_id}</td>
                            <td className="reading px-3 py-2 text-right">
                              {s.tasks_completed}/{s.tasks_total}
                            </td>
                            <td className="px-3 py-2">
                              <div className="flex flex-wrap gap-1">
                                {s.alerts.length === 0 && <span className="text-ink-2">None</span>}
                                {s.alerts.slice(0, 3).map((a, i) => (
                                  <StatusBadge key={i} status={a.severity as SeverityLevel} label={a.title} />
                                ))}
                              </div>
                            </td>
                            <td className="px-3 py-2 text-office-small">
                              {(Object.entries(s.safety_events) as Array<[SafetyEventType, number]>).map(([k, n]) => `${SAFETY_WORD[k]} ×${n}`).join(', ') || '—'}
                            </td>
                            <td className="max-w-[320px] px-3 py-2 text-office-small text-ink-2">{s.in_progress ? 'Shift in progress' : s.handover_notes}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </DataState>
              </section>
            </>
          )
        }
      </DataState>
    </ManagerShell>
  );
}
