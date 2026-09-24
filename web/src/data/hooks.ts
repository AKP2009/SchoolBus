/**
 * The only data entry point for screens. Every hook picks mocks (web/src/mocks, built by
 * scripts/build_mocks.py) or the real source (Supabase + FastAPI in ./api.ts) from
 * VITE_USE_MOCKS, so switching to real data touches nothing outside web/src/data/.
 */
import { useEffect, useMemo, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { enqueue, flush, type QueuedWrite } from '@/lib/offlineQueue';
import { stepsFor } from '@/lib/alertSteps';
import { supabase } from '@/lib/supabase';
import { STAGE_RANK, useAlertOverlay, useAlerts } from '@/stores/alerts';
import { useConnection } from '@/stores/connection';
import { useSession } from '@/stores/session';
import type {
  Alert,
  AlertRow,
  ChatAnswer,
  Clusters,
  FatigueRow,
  Fleet,
  FleetMachine,
  Geofence,
  Handover,
  IncidentRow,
  MachineHealth,
  MachineLogs,
  MachineSignals,
  MaintenancePrediction,
  PlanResult,
  SafetyEventRow,
  ShiftRow,
  TaskPrediction,
  TaskRow,
  Training,
  TrainingRecord,
  World,
} from '@/types/domain';
import * as api from './api';
import { MOCK_LATENCY_MS, USE_MOCKS } from './config';
import { streamAlertIds, useLive } from './live';
import { mock } from './mocks';
import { getCached, setCached, useResource, type Resource } from './resource';

const wait = <T,>(v: T | Promise<T>) =>
  new Promise<T>((res, rej) => window.setTimeout(() => Promise.resolve(v).then(res, rej), MOCK_LATENCY_MS));

/** Mock: load the file; real: the api call. */
function pick<T>(mocked: () => Promise<T>, real: () => Promise<T>): () => Promise<T> {
  return USE_MOCKS ? () => wait(mocked()) : real;
}

export function uuid(): string {
  return crypto.randomUUID();
}

// ---------------------------------------------------------------------------------------------
// World / session
// ---------------------------------------------------------------------------------------------
/** Site, demo operator/machine/shift, weather now. Real mode assembles it from the cab session. */
export function useWorld(): Resource<World> {
  const cab = useSession((s) => s.cab);
  return useResource<World>(
    'world',
    pick(
      () => mock('world'),
      async () => {
        if (!supabase) throw new Error('Supabase is not configured.');
        const site = cab?.siteId ?? 'S1';
        const [sites, weather] = await Promise.all([
          supabase.from('sites').select('*'),
          supabase.from('weather').select('*').eq('site_id', site).order('ts', { ascending: false }).limit(1),
        ]);
        const s = (sites.data ?? []) as World['sites'];
        return {
          now: new Date().toISOString(),
          stream_from: new Date().toISOString(),
          stream_minutes: 0,
          site: s.find((x) => x.site_id === site) ?? { site_id: site, name: site },
          sites: s,
          operator: { operator_id: cab?.operatorId ?? '', full_name: cab?.operatorName ?? '', experience_years: 0, certification_level: 0, languages: ['en'], preferred_shift: cab?.shiftType ?? 'day' },
          manager: { name: '', role: 'manager', site_id: site },
          machine: { machine_id: cab?.machineId ?? '', machine_type: (cab?.machineType ?? 'excavator') as World['machine']['machine_type'], model: '', serial_no: '', year: 0 },
          shift: { shift_id: cab?.shiftId ?? '', shift_type: cab?.shiftType ?? 'day', shift_date: '', start_time: cab?.shiftStart ?? '', end_time: cab?.shiftEnd ?? '', fuel_start_pct: cab?.fuelStartPct ?? 0 },
          weather: ((weather.data ?? [])[0] ?? {}) as World['weather'],
        };
      },
    ),
    undefined as unknown as World,
  );
}

/** Sign in: mock accepts the demo operator; real uses Supabase Auth and the operator's latest shift. */
export async function signInOperator(email: string, password: string): Promise<void> {
  const signIn = useSession.getState().signIn;
  if (USE_MOCKS) {
    const w = await wait(mock('world'));
    if (password.length < 4) throw new Error('Wrong email or password.');
    signIn({
      operatorId: w.operator.operator_id,
      operatorName: w.operator.full_name,
      machineId: w.machine.machine_id,
      machineType: w.machine.machine_type,
      shiftId: w.shift.shift_id,
      shiftType: w.shift.shift_type,
      shiftStart: w.shift.start_time,
      shiftEnd: w.shift.end_time,
      siteId: w.site.site_id,
      fuelStartPct: w.shift.fuel_start_pct,
      handoverSeen: false,
    });
    return;
  }
  const profile = await api.signIn(email, password);
  if (profile.role !== 'operator' || !profile.operator_id) throw new Error('This account is not an operator account.');
  const shift = await api.currentShift(profile.operator_id);
  if (!shift) throw new Error('No shift is assigned to you. Ask your supervisor.');
  const { data: m } = await supabase!.from('machines').select('machine_type').eq('machine_id', shift.machine_id).single();
  signIn({
    operatorId: profile.operator_id,
    operatorName: profile.full_name ?? profile.operator_id,
    machineId: shift.machine_id,
    machineType: (m as { machine_type: string } | null)?.machine_type ?? 'excavator',
    shiftId: shift.shift_id,
    shiftType: shift.shift_type,
    shiftStart: shift.start_time,
    shiftEnd: shift.end_time,
    siteId: shift.site_id,
    fuelStartPct: shift.fuel_start_pct,
    handoverSeen: false,
  });
}

// ---------------------------------------------------------------------------------------------
// Operator: tasks, handover, logs
// ---------------------------------------------------------------------------------------------
export function useTasks(shiftId: string | undefined): Resource<TaskRow[]> {
  const cab = useSession((s) => s.cab);
  const res = useResource<TaskRow[]>(
    shiftId ? `tasks:${shiftId}` : null,
    pick(
      () => mock('tasks'),
      () => api.tasks(cab!.operatorId, cab!.shiftStart.slice(0, 10)),
    ),
    [],
  );
  const clock = useLive((s) => s.clock);
  // Mock: rows the operator hasn't touched follow the replay clock (the snapshot is at the stream's end).
  const data = useMemo(() => {
    if (!USE_MOCKS || !res.data || !clock) return res.data;
    return res.data.map((t) => {
      if (Date.parse(t.updated_at) > Date.parse(t.scheduled_start ?? t.updated_at) + 864e5 || t.status === 'cancelled') return t;
      const started = t.actual_start != null && Date.parse(t.actual_start) <= clock;
      const ended = t.actual_end != null && Date.parse(t.actual_end) <= clock;
      if (ended) return t;
      if (started) return { ...t, status: 'in_progress' as const, actual_end: null, actual_duration_min: null, delay_reason: null };
      return { ...t, status: 'scheduled' as const, actual_start: null, actual_end: null, actual_duration_min: null, delay_reason: null };
    });
  }, [res.data, clock]);
  return { ...res, data };
}

/** Operator's usual time and expected efficiency per task (POST /predict/task-time). */
export function useTaskPredictions(taskIds: string[]): Resource<TaskPrediction[]> {
  const key = taskIds.length ? `pred:${taskIds.join(',')}` : null;
  return useResource<TaskPrediction[]>(
    key,
    pick(
      async () => (await mock('task_predictions')).predictions,
      async () => (await api.predictTaskTime(taskIds)).predictions,
    ),
    [],
  );
}

export type TaskPatch = Partial<Pick<TaskRow, 'status' | 'actual_start' | 'actual_end' | 'delay_reason'>>;

/** Start / complete / delay. Written through the offline queue so nothing is lost without signal. */
export async function updateTask(shiftId: string, taskId: string, patch: TaskPatch): Promise<{ queued: boolean }> {
  setCached<TaskRow[]>(`tasks:${shiftId}`, (prev) =>
    (prev ?? []).map((t) => (t.task_id === taskId ? { ...t, ...patch, updated_at: new Date().toISOString() } : t)),
  );
  return { queued: await write({ client_id: uuid(), table: 'tasks', payload: { task_id: taskId, ...patch } }) };
}

export function useHandover(machineId: string | undefined, shiftStart: string | undefined): Resource<Handover | null> {
  return useResource<Handover | null>(
    machineId ? `handover:${machineId}:${shiftStart}` : null,
    pick(
      () => mock('handover'),
      async () => {
        const s = await api.handoverShift(machineId!, shiftStart!);
        if (!s) return null;
        const summary = s.handover_summary ?? (await api.generateHandover(s.shift_id).then((r) => r.summary).catch(() => null));
        return {
          shift_id: s.shift_id,
          machine_id: s.machine_id,
          operator_id: s.operator_id,
          operator_name: null,
          shift_type: s.shift_type,
          start_time: s.start_time,
          end_time: s.end_time,
          fuel_start_pct: s.fuel_start_pct,
          fuel_end_pct: s.fuel_end_pct,
          handover_notes: s.handover_notes,
          issues_reported: s.issues_reported ?? [],
          handover_summary: summary,
          handover_generated_at: null,
          unfinished_tasks: [],
          alerts: [],
          safety_event_count: 0,
        };
      },
    ),
    null,
  );
}

export function useMachineLogs(machineId: string | undefined): Resource<MachineLogs | null> {
  return useResource<MachineLogs | null>(
    machineId ? `logs:${machineId}` : null,
    pick(
      async () => {
        const logs = await mock('machine_logs');
        return logs.machine_id === machineId ? logs : null;
      },
      async () => {
        const since = new Date(Date.now() - 7 * 864e5).toISOString();
        const [shifts, alerts, incidents] = await Promise.all([
          api.machineShifts(machineId!, since),
          api.machineAlerts(machineId!, since),
          api.machineIncidents(machineId!, since),
        ]);
        return {
          machine_id: machineId!,
          from: since,
          to: new Date().toISOString(),
          shifts: shifts.map((s) => {
            const inside = (ts: string) => ts >= s.start_time && ts < s.end_time;
            return {
              ...s,
              operator_name: null,
              in_progress: Date.parse(s.end_time) > Date.now(),
              issues_reported: s.issues_reported ?? [],
              tasks_completed: 0,
              tasks_total: 0,
              alerts: alerts
                .filter((a) => inside(a.ts))
                .map((a) => ({ alert_code: a.alert_code, title: a.title, severity: a.severity, max_stage: a.stage, ts: a.ts, resolved_at: a.resolved_at })),
              safety_events: {},
              incidents: incidents.filter((i) => inside(i.ts)),
            };
          }),
        };
      },
    ),
    null,
  );
}

// ---------------------------------------------------------------------------------------------
// Machine health
// ---------------------------------------------------------------------------------------------
export function useFleetHealth(): Resource<MachineHealth[]> {
  return useResource<MachineHealth[]>(
    'health:all',
    pick(
      () => mock('machine_health'),
      async () =>
        (await api.healthLatest()).map((h) => ({
          machine_id: String(h.machine_id),
          ts: String(h.ts),
          overall: Number(h.overall_score),
          subsystems: {
            engine: Number(h.engine_score ?? 1),
            cooling: Number(h.cooling_score ?? 1),
            hydraulics: Number(h.hydraulics_score ?? 1),
            electrical: Number(h.electrical_score ?? 1),
            undercarriage: Number(h.undercarriage_score ?? 1),
          },
          anomaly_score: (h.anomaly_score as number | null) ?? null,
          failure_probability: null,
          likely_component: null,
        })),
    ),
    [],
  );
}

export function useMachineHealth(machineId: string | undefined): Resource<MachineHealth | null> {
  return useResource<MachineHealth | null>(
    machineId ? `health:${machineId}` : null,
    pick(
      async () => (await mock('machine_health')).find((h) => h.machine_id === machineId) ?? null,
      () => api.machineHealth(machineId!),
    ),
    null,
  );
}

/** Last 60 minutes of the signals behind each twin region. */
export function useMachineSignals(machineId: string | undefined): Resource<MachineSignals | null> {
  return useResource<MachineSignals | null>(
    machineId ? `signals:${machineId}` : null,
    pick(
      async () => {
        const s = await mock('machine_signals');
        return s.machine_id === machineId ? s : null;
      },
      async () => null,
    ),
    null,
  );
}

// ---------------------------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------------------------
/**
 * Open alerts for the site. Real: v_open_alerts + Realtime. Mock: the snapshot at the mock "now",
 * where alerts the recorded stream carries follow the replay clock (they appear, escalate and
 * resolve as the demo stream plays).
 */
export function useOpenAlerts(siteId: string | undefined): Resource<AlertRow[]> {
  const res = useResource<AlertRow[]>(siteId ? `alerts:${siteId}` : null, pick(() => mock('alerts_open'), () => api.openAlerts(siteId!)), []);
  const [liveAlerts, clock, liveMachine] = useLive(useShallow((s) => [s.alerts, s.clock, s.machineId] as const));
  const overlay = useAlertOverlay();
  const [realtime, setRealtime] = useState<Record<number, AlertRow>>({});

  useEffect(() => {
    if (USE_MOCKS || !siteId) return;
    return api.subscribeAlerts(siteId, (row) => setRealtime((r) => ({ ...r, [row.id]: row })));
  }, [siteId]);

  const data = useMemo(() => {
    if (!res.data) return res.data;
    let rows: AlertRow[];
    if (USE_MOCKS) {
      const ids = streamAlertIds();
      const byId = new Map(res.data.map((r) => [r.id, r]));
      rows = [];
      for (const r of res.data) {
        if (ids.has(r.id)) {
          const l = liveAlerts[r.id];
          if (l && l.stage !== 'resolved') rows.push({ ...r, stage: l.stage, title: l.title, recommended_action: l.recommended_action, severity: l.severity });
        } else if (!clock || Date.parse(r.ts) <= clock) rows.push(r);
      }
      for (const l of Object.values(liveAlerts)) {
        if (byId.has(l.id) || l.stage === 'resolved') continue;
        rows.push({
          id: l.id,
          ts: new Date(l.at).toISOString(),
          site_id: siteId ?? '',
          machine_id: liveMachine,
          operator_id: null,
          source: l.alert_code.startsWith('PROXIMITY') || l.alert_code.startsWith('FATIGUE') ? 'vision' : 'rule',
          category: l.alert_code === 'SOS' ? 'emergency' : 'safety',
          alert_code: l.alert_code,
          title: l.title,
          message: l.title,
          recommended_action: l.recommended_action,
          severity: l.severity,
          stage: l.stage,
          anomaly_score: null,
          evidence: null,
          acknowledged_by: null,
          acknowledged_at: null,
          resolved_at: null,
        });
      }
    } else {
      const merged = new Map(res.data.map((r) => [r.id, r]));
      for (const r of Object.values(realtime)) merged.set(r.id, r);
      rows = [...merged.values()].filter((r) => !r.resolved_at);
    }
    return rows
      .filter((r) => !overlay.resolved[r.id])
      .map((r) => (overlay.acked[r.id] && !r.acknowledged_at ? { ...r, acknowledged_at: overlay.acked[r.id]! } : r))
      .sort((a, b) => b.ts.localeCompare(a.ts));
  }, [res.data, liveAlerts, clock, liveMachine, overlay, realtime, siteId]);

  return { ...res, data };
}

export async function acknowledgeAlert(id: number): Promise<void> {
  useAlertOverlay.getState().ack(id);
  if (!USE_MOCKS) await api.acknowledgeAlert(id);
}
export async function resolveAlert(id: number): Promise<void> {
  useAlertOverlay.getState().resolve(id);
  if (!USE_MOCKS) await api.resolveAlert(id);
}

export interface CabAlerts {
  takeovers: Alert[];
  banner: Alert | null;
  acknowledge: (a: Alert) => void;
  dismiss: (a: Alert) => void;
}

/** The cab's alert queue: stream alerts for this machine + local ones (SOS), minus what was handled. */
export function useCabAlerts(machineType: string | undefined): CabAlerts {
  const live = useLive((s) => s.alerts);
  const { local, acked, dismissed, acknowledge, dismiss } = useAlerts();
  return useMemo(() => {
    const all: Alert[] = [
      ...Object.values(live)
        .filter((a) => a.stage !== 'resolved')
        .map((a) => ({
          id: a.id,
          ts: new Date(a.at).toISOString(),
          machine_id: null,
          title: a.title,
          message: a.title,
          recommended_action: a.recommended_action,
          severity: a.severity,
          stage: a.stage,
          steps: stepsFor(a.alert_code, a.stage, machineType),
        })),
      ...local,
    ];
    const handled = (a: Alert, rec: Record<number, Alert['stage']>) => rec[a.id] != null && STAGE_RANK[a.stage] <= STAGE_RANK[rec[a.id]!];
    const takeovers = all.filter((a) => (a.severity === 'critical' || a.severity === 'emergency') && !handled(a, acked));
    const banners = all.filter((a) => (a.severity === 'warning' || a.severity === 'info') && !handled(a, dismissed)).sort((a, b) => b.ts.localeCompare(a.ts));
    return {
      takeovers,
      banner: banners[0] ?? null,
      acknowledge: (a) => {
        acknowledge(a.id, a.stage);
        if (!USE_MOCKS && a.id > 0) void api.acknowledgeAlert(a.id).catch(() => undefined);
      },
      dismiss: (a) => dismiss(a.id, a.stage),
    };
  }, [live, local, acked, dismissed, acknowledge, dismiss, machineType]);
}

// ---------------------------------------------------------------------------------------------
// Safety, fatigue, incidents
// ---------------------------------------------------------------------------------------------
const WEEK_AGO = () => new Date(Date.now() - 14 * 864e5).toISOString();

export function useSafetyEvents(siteId: string | undefined): Resource<SafetyEventRow[]> {
  return useResource<SafetyEventRow[]>(siteId ? `safety:${siteId}` : null, pick(() => mock('safety_events'), () => api.safetyEvents(siteId!, WEEK_AGO())), []);
}

export function useShifts(siteId: string | undefined): Resource<ShiftRow[]> {
  return useResource<ShiftRow[]>(siteId ? `shifts:${siteId}` : null, pick(() => mock('shifts'), () => api.shifts(siteId!, WEEK_AGO())), []);
}

export function useFatigue(shiftId: string | undefined): Resource<FatigueRow[]> {
  return useResource<FatigueRow[]>(shiftId ? `fatigue:${shiftId}` : null, pick(() => mock('fatigue'), () => api.fatigue(shiftId!)), []);
}

export function useIncidents(siteId: string | undefined): Resource<IncidentRow[]> {
  return useResource<IncidentRow[]>(siteId ? `incidents:${siteId}` : null, pick(() => mock('incidents'), () => api.incidents(siteId!)), []);
}

export type NewIncident = Pick<IncidentRow, 'incident_type' | 'severity' | 'description' | 'injury' | 'machine_id' | 'operator_id' | 'site_id'> &
  Partial<Pick<IncidentRow, 'voice_transcript' | 'reported_via'>>;

/** Returns the stored row; queued (and synced later) when offline. Idempotent by client_id. */
export async function reportIncident(input: NewIncident): Promise<{ row: IncidentRow; queued: boolean }> {
  const row: IncidentRow = {
    id: -Date.now(),
    client_id: uuid(),
    ts: new Date().toISOString(),
    damage_description: null,
    root_cause: null,
    reported_via: 'form',
    voice_transcript: null,
    ai_summary: null,
    linked_alert_id: null,
    linked_event_id: null,
    media_paths: [],
    status: 'open',
    created_by: null,
    ...input,
  };
  setCached<IncidentRow[]>(`incidents:${input.site_id}`, (prev) => [row, ...(prev ?? [])]);
  const queued = await write({ client_id: row.client_id, table: 'incidents', payload: stripLocal(row) });
  return { row, queued };
}

function stripLocal(row: IncidentRow): Record<string, unknown> {
  const { id: _id, ...rest } = row;
  return rest;
}

// ---------------------------------------------------------------------------------------------
// Offline-capable writes (CLAUDE.md rule 7)
// ---------------------------------------------------------------------------------------------
const pendingIds = new Set<string>();

export function isPending(clientId: string): boolean {
  return pendingIds.has(clientId);
}

async function send(w: QueuedWrite): Promise<void> {
  if (USE_MOCKS) return;
  if (w.table === 'incidents') await api.upsertIncident(w.payload);
  else if (w.table === 'training_records') await api.upsertTrainingRecord(w.payload);
  else {
    const { task_id, ...patch } = w.payload as { task_id: string } & TaskPatch;
    await api.updateTask(task_id, patch);
  }
}

/** Sends now when online, else queues in IndexedDB. Returns true when queued. */
async function write(w: Omit<QueuedWrite, 'queued_at'>): Promise<boolean> {
  if (useConnection.getState().online) {
    try {
      await send({ ...w, queued_at: new Date().toISOString() });
      return false;
    } catch {
      /* fall through to the queue */
    }
  }
  pendingIds.add(w.client_id);
  await enqueue(w);
  return true;
}

/** Replays the queue when the connection comes back. Mount once (App). */
export function useOfflineSync(): void {
  const online = useConnection((s) => s.online);
  useEffect(() => {
    if (!online) return;
    void flush(async (w) => {
      await send(w);
      pendingIds.delete(w.client_id);
    }).catch(() => undefined);
  }, [online]);
}

// ---------------------------------------------------------------------------------------------
// Manager: fleet, maintenance, clusters, geofences
// ---------------------------------------------------------------------------------------------
export function useFleet(siteId: string | undefined): Resource<Fleet | null> {
  return useResource<Fleet | null>(
    siteId ? `fleet:${siteId}` : null,
    pick(
      async () => {
        const f = await mock('fleet');
        return { ...f, machines: f.machines.filter((m) => m.site_id === siteId) };
      },
      async () => {
        const [machines, latest, health, fences, alerts] = await Promise.all([
          api.machines(siteId!),
          api.latestTelemetry(),
          api.healthLatest(),
          api.geofences(siteId!),
          api.openAlerts(siteId!),
        ]);
        const rank = { info: 0, warning: 1, critical: 2, emergency: 3 } as const;
        return {
          now: new Date().toISOString(),
          geofences: fences,
          machines: machines.map((m): FleetMachine => {
            const id = String(m.machine_id);
            const l = latest.find((x) => x.machine_id === id);
            const h = health.find((x) => x.machine_id === id);
            const mine = alerts.filter((a) => a.machine_id === id);
            const onShift = !!l && Date.now() - Date.parse(String(l.ts)) < 5 * 60_000;
            return {
              ...(m as unknown as FleetMachine),
              live: {
                ts: (l?.ts as string) ?? null,
                on_shift: onShift,
                operator_id: onShift ? ((l?.operator_id as string) ?? null) : null,
                operator_name: null,
                shift_id: (l?.shift_id as string) ?? null,
                gps_lat: (l?.gps_lat as number) ?? null,
                gps_lon: (l?.gps_lon as number) ?? null,
                fuel_level_pct: (l?.fuel_level_pct as number) ?? null,
                ground_speed_kmh: (l?.ground_speed_kmh as number) ?? null,
                is_idle: (l?.is_idle as boolean) ?? null,
              },
              health_overall: h ? Number(h.overall_score) : null,
              open_alerts: mine.length,
              worst_severity: mine.reduce<FleetMachine['worst_severity']>((w, a) => (!w || rank[a.severity] > rank[w] ? a.severity : w), null),
            };
          }),
        };
      },
    ),
    null,
  );
}

export function useMaintenance(): Resource<MaintenancePrediction[]> {
  return useResource<MaintenancePrediction[]>('maintenance', pick(() => mock('maintenance_predictions'), () => api.maintenance()), []);
}

export function useClusters(): Resource<Clusters | null> {
  return useResource<Clusters | null>(
    'clusters',
    pick(
      () => mock('clusters'),
      // PCA points only exist in ml/artifacts/clustering (no endpoint yet): metrics are live, the scatter is the artifact's.
      async () => ({ ...(await mock('clusters')), metrics: await api.fleetMetrics() }),
    ),
    null,
  );
}

export async function verifyOutlier(id: number): Promise<void> {
  setCached<Clusters | null>('clusters', (c) =>
    c ? { ...c, metrics: c.metrics.map((m) => (m.id === id ? { ...m, verified_at: new Date().toISOString(), verified_by: 'me' } : m)) } : (c ?? null),
  );
  if (!USE_MOCKS) await api.verifyOutlier(id);
}

export async function saveGeofence(siteId: string, g: Omit<Geofence, 'id' | 'created_at' | 'created_by'>): Promise<Geofence> {
  const saved: Geofence = USE_MOCKS ? { ...g, id: Date.now(), created_at: new Date().toISOString(), created_by: null } : await api.saveGeofence(g);
  setCached<Fleet | null>(`fleet:${siteId}`, (f) => (f ? { ...f, geofences: [...f.geofences, saved] } : (f ?? null)));
  return saved;
}

export async function setGeofenceActive(siteId: string, id: number, active: boolean): Promise<void> {
  setCached<Fleet | null>(`fleet:${siteId}`, (f) => (f ? { ...f, geofences: f.geofences.map((g) => (g.id === id ? { ...g, active } : g)) } : (f ?? null)));
  if (!USE_MOCKS) await api.setGeofenceActive(id, active);
}

// ---------------------------------------------------------------------------------------------
// Plan re-evaluation
// ---------------------------------------------------------------------------------------------
export function usePlan(shiftId: string | undefined, reason = 'fatigue_high'): Resource<PlanResult | null> {
  const res = useResource<PlanResult | null>(
    shiftId ? `plan:${shiftId}` : null,
    pick(() => mock('plan'), () => api.reEvaluatePlan(shiftId!, reason)),
    null,
  );
  const clock = useLive((s) => s.clock);
  // Mock: the re-evaluation happened at the snapshot time; don't show it earlier in the replay.
  const early = USE_MOCKS && res.data?.now != null && clock > 0 && clock < Date.parse(res.data.now);
  return early ? { ...res, data: null } : res;
}

export async function acceptPlan(shiftId: string, plan: PlanResult): Promise<void> {
  if (!USE_MOCKS) await api.acceptPlan(shiftId);
  setCached<TaskRow[]>(`tasks:${shiftId}`, (prev) => {
    const order = new Map(plan.new_order.map((id, i) => [id, i]));
    return (prev ?? [])
      .map((t) => (plan.moved_to_next_shift.includes(t.task_id) ? { ...t, status: 'cancelled' as const, delay_reason: 'Moved to next shift' } : t))
      .sort((a, b) => (order.get(a.task_id) ?? 99) - (order.get(b.task_id) ?? 99) || a.sequence_no - b.sequence_no);
  });
  setCached<PlanResult | null>(`plan:${shiftId}`, () => null);
}

// ---------------------------------------------------------------------------------------------
// Training + chat
// ---------------------------------------------------------------------------------------------
export function useTraining(operatorId: string | undefined): Resource<Training | null> {
  return useResource<Training | null>(operatorId ? `training:${operatorId}` : null, pick(() => mock('training'), () => api.training(operatorId!)), null);
}

export async function saveTrainingResult(operatorId: string, moduleId: string, score: number): Promise<void> {
  const rec: TrainingRecord = {
    id: -Date.now(),
    client_id: uuid(),
    operator_id: operatorId,
    module_id: moduleId,
    started_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
    score,
    passed: score >= 70,
  };
  setCached<Training | null>(`training:${operatorId}`, (t) => (t ? { ...t, records: [rec, ...t.records] } : (t ?? null)));
  const { id: _id, ...payload } = rec;
  await write({ client_id: rec.client_id, table: 'training_records', payload });
}

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9ऀ-ॿ ]/g, ' ').split(/\s+/).filter((w) => w.length > 2);

/** POST /chat. Mock: the cached answer whose question shares the most words, else "not in the manuals". */
export async function sendChat(operatorId: string, message: string, sessionId: string): Promise<ChatAnswer> {
  if (!USE_MOCKS) return api.chat({ session_id: sessionId, operator_id: operatorId, message, language: 'en' });
  const { examples } = await wait(mock('chat'));
  const words = new Set(norm(message));
  const codes = message.match(/E-\d{3}/gi)?.map((c) => c.toUpperCase()) ?? [];
  let best: (typeof examples)[number] | undefined;
  let bestScore = 0;
  for (const ex of examples) {
    let score = norm(ex.question).filter((w) => words.has(w)).length;
    if (codes.length && codes.some((c) => ex.question.toUpperCase().includes(c))) score += 5;
    if (codes.length && !codes.some((c) => ex.question.toUpperCase().includes(c)) && /E-\d{3}/i.test(ex.question)) score -= 5;
    if (score > bestScore) [best, bestScore] = [ex, score];
  }
  if (best && bestScore >= 2) return { answer: best.answer, sources: best.sources };
  return { answer: "That isn't in the manuals I have. Ask your supervisor or maintenance before you go ahead.", sources: [] };
}

export async function chatSuggestions(): Promise<string[]> {
  if (!USE_MOCKS) return ['What does E-365 mean?', 'Someone walked behind my machine. What do I do?', 'I feel very sleepy on night shift.'];
  return (await mock('chat')).examples.map((e) => e.question);
}

export { getCached };
