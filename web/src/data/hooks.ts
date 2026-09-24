/**
 * The only data entry point for screens. Every hook picks mocks (web/src/mocks, built by
 * scripts/build_mocks.py) or the real source (Supabase + FastAPI in ./api.ts, Realtime in
 * ./realtime.ts, the stream in ./live.ts) from VITE_USE_MOCKS, so switching to real data touches
 * nothing outside web/src/data/. Live mode works in data time (./live.ts `dataNowMs`).
 */
import { useEffect, useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { getLanguage, setLanguage, t, useLanguage } from '@/i18n';
import { enqueue, flush, queuedIds, refreshCount, type QueuedWrite } from '@/lib/offlineQueue';
import { stepsFor } from '@/lib/alertSteps';
import { asApp, currentApp } from '@/lib/supabase';
import { STAGE_RANK, useAlertOverlay, useAlerts } from '@/stores/alerts';
import { useConnection } from '@/stores/connection';
import { useSession } from '@/stores/session';
import type { TablesInsert } from '@/types/supabase';
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
  LogAlert,
  MachineHealth,
  MachineLogs,
  MachineSignals,
  MachineType,
  MaintenancePrediction,
  PlanResult,
  SafetyEventRow,
  SafetyEventType,
  SeverityLevel,
  ShiftRow,
  TaskPrediction,
  TaskRow,
  Training,
  TrainingRecord,
  World,
} from '@/types/domain';
import * as api from './api';
import { signInWithRole, useAuth, type Profile } from './auth';
import { MOCK_LATENCY_MS, USE_MOCKS } from './config';
import { dataNowMs, setReplayStatus, streamAlertIds, useLive } from './live';
import { mock } from './mocks';
import { fleetKeys, useRealtimeAlerts } from './realtime';
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

const iso = (ms: number) => new Date(ms).toISOString();

/** The signed-in office profile's site (live), else S1. */
function officeSite(): string {
  return useAuth.getState().office.profile?.site_id ?? 'S1';
}

// ---------------------------------------------------------------------------------------------
// World / session
// ---------------------------------------------------------------------------------------------
/** Site, demo operator/machine/shift, weather now. Live mode assembles it from the cab session / office profile. */
export function useWorld(): Resource<World> {
  const cab = useSession((s) => s.cab);
  const office = useAuth((s) => s.office.profile);
  const app = currentApp();
  const who = app === 'cab' ? (cab?.shiftId ?? null) : (office?.id ?? null);
  return useResource<World>(
    USE_MOCKS ? 'world' : who ? `world:${app}:${who}` : null,
    pick(
      () => mock('world'),
      async () => {
        const inCab = app === 'cab' && !!cab;
        const site = inCab ? cab.siteId : (office?.site_id ?? 'S1');
        const [sites, weather, op, m] = await Promise.all([
          api.sites(),
          api.weatherAt(site, iso(dataNowMs())),
          inCab ? api.operator(cab.operatorId) : Promise.resolve(null),
          inCab ? api.machine(cab.machineId) : Promise.resolve(null),
        ]);
        return {
          now: iso(dataNowMs()),
          stream_from: iso(dataNowMs()),
          stream_minutes: 0,
          site: sites.find((x) => x.site_id === site) ?? { site_id: site, name: site },
          sites,
          operator: {
            operator_id: cab?.operatorId ?? '',
            full_name: op?.full_name ?? cab?.operatorName ?? '',
            experience_years: Number(op?.experience_years ?? 0),
            certification_level: op?.certification_level ?? 0,
            languages: op?.languages ?? ['en'],
            preferred_shift: op?.preferred_shift ?? cab?.shiftType ?? 'day',
          },
          manager: { name: office?.full_name ?? 'Site manager', role: office?.role ?? 'manager', site_id: site },
          machine: {
            machine_id: m?.machine_id ?? cab?.machineId ?? '',
            machine_type: (m?.machine_type ?? cab?.machineType ?? 'excavator') as MachineType,
            model: m?.model ?? '',
            serial_no: m?.serial_no ?? '',
            year: m?.year ?? 0,
          },
          shift: {
            shift_id: cab?.shiftId ?? '',
            shift_type: cab?.shiftType ?? 'day',
            shift_date: cab?.shiftStart.slice(0, 10) ?? '',
            start_time: cab?.shiftStart ?? '',
            end_time: cab?.shiftEnd ?? '',
            fuel_start_pct: cab?.fuelStartPct ?? 0,
          },
          weather: (weather ?? { ts: iso(dataNowMs()), temp_c: 0, humidity_pct: 0, rain_mm: 0, wind_kmh: 0, visibility_m: 0, dust_index: 0 }) as World['weather'],
        };
      },
    ),
    undefined as unknown as World,
  );
}

/**
 * Sign in. Mock: accepts the demo operator, as before. Live: Supabase Auth, and `profiles.role`
 * decides where the account goes (the caller routes with `homeFor`). An operator gets the cab
 * session for their shift at data time (the running replay's clock, else VITE_DATA_NOW), so Ganesh
 * lands on SH-2026-08-19-M05-N for the demo window.
 */
export async function signIn(email: string, password: string): Promise<Profile['role']> {
  const signInCab = useSession.getState().signIn;
  if (USE_MOCKS) {
    const w = await wait(mock('world'));
    if (password.length < 4) throw new Error('Wrong email or password.');
    signInCab({
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
    return 'operator';
  }
  const profile = await signInWithRole(email, password);
  if (profile.role !== 'operator') return profile.role;
  if (!profile.operator_id) throw new Error('This operator account has no operator ID. Ask the site admin.');
  setLanguage(profile.preferred_language);
  const operatorId = profile.operator_id;
  const { shift, m, name } = await asApp('cab', async () => {
    await api.replayStatus().then(setReplayStatus, () => undefined); // data time from a running replay
    const shift = await api.shiftAt(operatorId, iso(dataNowMs()));
    if (!shift) throw new Error('No shift is assigned to you. Ask your supervisor.');
    const m = await api.machine(shift.machine_id);
    return { shift, m, name: profile.full_name ?? (await api.operatorName(operatorId)) };
  });
  signInCab({
    operatorId,
    operatorName: name ?? operatorId,
    machineId: shift.machine_id,
    machineType: m.machine_type,
    shiftId: shift.shift_id,
    shiftType: shift.shift_type,
    shiftStart: shift.start_time,
    shiftEnd: shift.end_time,
    siteId: shift.site_id,
    fuelStartPct: shift.fuel_start_pct,
    handoverSeen: false,
  });
  return 'operator';
}

/** Mock QA `?as=OP02`: signs the cab in as the demo operator. */
export async function signInOperator(email: string, password: string): Promise<void> {
  await signIn(email, password);
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
      () => api.tasks(cab!.operatorId, shiftId!),
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

function logAlerts(rows: AlertRow[]): LogAlert[] {
  return rows.map((a) => ({ alert_code: a.alert_code, title: a.title, severity: a.severity, max_stage: a.stage, ts: a.ts, resolved_at: a.resolved_at }));
}

export function useHandover(machineId: string | undefined, shiftStart: string | undefined): Resource<Handover | null> {
  return useResource<Handover | null>(
    machineId ? `handover:${machineId}:${shiftStart}` : null,
    pick(
      () => mock('handover'),
      async () => {
        const s = await api.handoverShift(machineId!, shiftStart!);
        if (!s) return null;
        const [summary, name, unfinished, alerts, events] = await Promise.all([
          s.handover_summary ? Promise.resolve(s.handover_summary) : api.generateHandover(s.shift_id).then((r) => r.summary, () => null),
          api.operatorName(s.operator_id),
          api.unfinishedTasks(s.shift_id),
          api.machineAlerts(machineId!, s.start_time, s.end_time),
          api.machineSafetyEvents(machineId!, s.start_time, s.end_time),
        ]);
        return {
          shift_id: s.shift_id,
          machine_id: s.machine_id,
          operator_id: s.operator_id,
          operator_name: name,
          shift_type: s.shift_type,
          start_time: s.start_time,
          end_time: s.end_time,
          fuel_start_pct: s.fuel_start_pct,
          fuel_end_pct: s.fuel_end_pct,
          handover_notes: s.handover_notes,
          issues_reported: s.issues_reported ?? [],
          handover_summary: summary,
          handover_generated_at: s.handover_generated_at,
          unfinished_tasks: unfinished as Handover['unfinished_tasks'],
          alerts: logAlerts(alerts),
          safety_event_count: events.length,
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
        const now = dataNowMs();
        const [since, until] = [iso(now - 7 * 864e5), iso(now)];
        const [shifts, alerts, incidents, events] = await Promise.all([
          api.machineShifts(machineId!, since, until),
          api.machineAlerts(machineId!, since, until),
          api.machineIncidents(machineId!, since, until),
          api.machineSafetyEvents(machineId!, since, until),
        ]);
        const [tasks, names] = await Promise.all([
          api.shiftTaskStatus(shifts.map((s) => s.shift_id)),
          api.operatorNames([...new Set(shifts.map((s) => s.operator_id))]),
        ]);
        return {
          machine_id: machineId!,
          from: since,
          to: until,
          shifts: shifts.map((s) => {
            const inside = (ts: string) => ts >= s.start_time && ts < s.end_time;
            const mine = tasks.filter((t) => t.shift_id === s.shift_id);
            const counts: Partial<Record<SafetyEventType, number>> = {};
            for (const e of events) if (inside(e.ts)) counts[e.event_type] = (counts[e.event_type] ?? 0) + 1;
            return {
              ...s,
              operator_name: names.get(s.operator_id) ?? null,
              in_progress: Date.parse(s.end_time) > now,
              issues_reported: s.issues_reported ?? [],
              tasks_completed: mine.filter((t) => t.status === 'completed').length,
              tasks_total: mine.length,
              alerts: logAlerts(alerts.filter((a) => inside(a.ts))),
              safety_events: counts,
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
          anomaly_score: h.anomaly_score,
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
  const realtime = useRealtimeAlerts((s) => s.rows);

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
      for (const r of Object.values(realtime)) if (r.site_id === siteId) merged.set(r.id, r);
      rows = [...merged.values()].filter((r) => !r.resolved_at && r.stage !== 'resolved');
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

type CodedAlert = Alert & { alert_code: string };

/**
 * The cab's alert queue: stream alerts for this machine + local ones (SOS), minus what was handled.
 * Live mode also merges the machine's open alerts at sign-in and its Realtime alert rows (by id, the
 * furthest stage wins), so an alert still reaches the cab while the stream reconnects.
 */
export function useCabAlerts(machineType: string | undefined): CabAlerts {
  const live = useLive((s) => s.alerts);
  const machineId = useSession((s) => s.cab?.machineId);
  const open = useResource<AlertRow[]>(!USE_MOCKS && machineId ? `cab-alerts:${machineId}` : null, () => api.openMachineAlerts(machineId!), []);
  const realtime = useRealtimeAlerts((s) => s.rows);
  const { local, acked, dismissed, acknowledge, dismiss } = useAlerts();
  const lang = useLanguage(); // takeover steps are in the cab's language
  return useMemo(() => {
    const merged = new Map<number, CodedAlert>();
    const put = (a: CodedAlert) => {
      const prev = merged.get(a.id);
      if (!prev || STAGE_RANK[a.stage] >= STAGE_RANK[prev.stage]) merged.set(a.id, a);
    };
    if (!USE_MOCKS) {
      for (const r of [...(open.data ?? []), ...Object.values(realtime)]) {
        if (r.machine_id !== machineId) continue;
        put({
          id: r.id,
          ts: r.ts,
          machine_id: r.machine_id,
          title: r.title,
          message: r.message,
          recommended_action: r.recommended_action,
          severity: r.severity,
          stage: r.resolved_at ? 'resolved' : r.stage,
          alert_code: r.alert_code,
        });
      }
    }
    for (const a of Object.values(live)) {
      put({
        id: a.id,
        ts: new Date(a.at).toISOString(),
        machine_id: null,
        title: a.title,
        message: a.title,
        recommended_action: a.recommended_action,
        severity: a.severity,
        stage: a.stage,
        alert_code: a.alert_code,
      });
    }
    const all: Alert[] = [
      ...[...merged.values()]
        .filter((a) => a.stage !== 'resolved')
        .map(({ alert_code, ...a }) => ({ ...a, steps: stepsFor(alert_code, a.stage, machineType) })),
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
  }, [live, open.data, realtime, machineId, local, acked, dismissed, acknowledge, dismiss, machineType, lang]);
}

// ---------------------------------------------------------------------------------------------
// Safety, fatigue, incidents
// ---------------------------------------------------------------------------------------------
/** The last 14 days of data time. */
const window14 = (): [string, string] => [iso(dataNowMs() - 14 * 864e5), iso(dataNowMs())];

export function useSafetyEvents(siteId: string | undefined): Resource<SafetyEventRow[]> {
  return useResource<SafetyEventRow[]>(siteId ? `safety:${siteId}` : null, pick(() => mock('safety_events'), () => api.safetyEvents(siteId!, ...window14())), []);
}

export function useShifts(siteId: string | undefined): Resource<ShiftRow[]> {
  return useResource<ShiftRow[]>(siteId ? `shifts:${siteId}` : null, pick(() => mock('shifts'), () => api.shifts(siteId!, ...window14())), []);
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
  const now = new Date().toISOString();
  const row: IncidentRow = {
    id: -Date.now(),
    client_id: uuid(),
    ts: now,
    damage_description: null,
    root_cause: null,
    reported_via: 'form',
    voice_transcript: null,
    ai_summary: null,
    linked_alert_id: null,
    linked_event_id: null,
    media_paths: [],
    status: 'open',
    created_by: useAuth.getState().cab.profile?.id ?? null,
    created_at: now,
    ...input,
  };
  setCached<IncidentRow[]>(`incidents:${input.site_id}`, (prev) => [row, ...(prev ?? [])]);
  const { id: _id, created_at: _created, ...payload } = row;
  const queued = await write({ client_id: row.client_id, table: 'incidents', payload });
  return { row, queued };
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
  if (w.table === 'incidents') await api.upsertIncident(w.payload as TablesInsert<'incidents'>);
  else if (w.table === 'training_records') await api.upsertTrainingRecord(w.payload as TablesInsert<'training_records'>);
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
      /* network or server trouble: fall through to the queue, nothing is lost */
    }
  }
  pendingIds.add(w.client_id);
  await enqueue(w);
  return true;
}

const SYNC_EVERY_MS = 30_000;

/**
 * Replays the queue at start, when the connection comes back, and every 30 s while online
 * (docs/supabase.md "Offline sync pattern"). Mount once (App).
 */
export function useOfflineSync(): void {
  const online = useConnection((s) => s.online);
  useEffect(() => {
    void refreshCount();
    void queuedIds().then(
      (ids) => ids.forEach((id) => pendingIds.add(id)),
      () => undefined,
    );
  }, []);
  useEffect(() => {
    if (!online) return;
    const run = () =>
      void flush(async (w) => {
        await send(w);
        pendingIds.delete(w.client_id);
      }).catch(() => undefined);
    run();
    const id = window.setInterval(run, SYNC_EVERY_MS);
    return () => window.clearInterval(id);
  }, [online]);
}

// ---------------------------------------------------------------------------------------------
// Manager: fleet, maintenance, clusters, geofences
// ---------------------------------------------------------------------------------------------
const SEVERITY_RANK: Record<SeverityLevel, number> = { info: 0, warning: 1, critical: 2, emergency: 3 };

export function useFleet(siteId: string | undefined): Resource<Fleet | null> {
  const key = siteId ? `fleet:${siteId}` : null;
  useEffect(() => {
    if (key) fleetKeys.add(key);
  }, [key]);
  const res = useResource<Fleet | null>(
    key,
    pick(
      async () => {
        const f = await mock('fleet');
        return { ...f, machines: f.machines.filter((m) => m.site_id === siteId) };
      },
      async () => {
        const now = dataNowMs();
        const [machines, health, fences, names] = await Promise.all([
          api.machines(siteId!),
          api.healthLatest(),
          api.geofences(siteId!),
          api.operatorNamesAtSite(siteId!),
        ]);
        const latest = await api.telemetryAt(
          machines.map((m) => m.machine_id),
          iso(now),
        );
        return {
          now: iso(now),
          geofences: fences,
          machines: machines.map((m, i): FleetMachine => {
            const l = latest[i];
            const h = health.find((x) => x.machine_id === m.machine_id);
            const onShift = !!l && now - Date.parse(l.ts) < 5 * 60_000;
            return {
              machine_id: m.machine_id,
              site_id: m.site_id,
              machine_type: m.machine_type,
              model: m.model,
              status: m.status,
              total_engine_hours: Number(m.total_engine_hours),
              hours_since_service: Number(m.hours_since_service),
              service_interval_hours: Number(m.service_interval_hours),
              live: {
                ts: l?.ts ?? null,
                on_shift: onShift,
                operator_id: onShift ? (l?.operator_id ?? null) : null,
                operator_name: onShift && l?.operator_id ? (names.get(l.operator_id) ?? null) : null,
                shift_id: l?.shift_id ?? null,
                gps_lat: l?.gps_lat ?? null,
                gps_lon: l?.gps_lon ?? null,
                fuel_level_pct: l?.fuel_level_pct ?? null,
                ground_speed_kmh: l?.ground_speed_kmh ?? null,
                is_idle: l?.is_idle ?? null,
              },
              health_overall: h?.overall_score != null ? Number(h.overall_score) : null,
              open_alerts: 0,
              worst_severity: null,
            };
          }),
        };
      },
    ),
    null,
  );
  // Live: alert counts follow the open-alerts feed (v_open_alerts + Realtime), so M05 turns red on the map as it happens.
  const alerts = useOpenAlerts(USE_MOCKS ? undefined : siteId);
  const data = useMemo(() => {
    if (USE_MOCKS || !res.data) return res.data;
    const open = alerts.data ?? [];
    return {
      ...res.data,
      machines: res.data.machines.map((m) => {
        const mine = open.filter((a) => a.machine_id === m.machine_id);
        return {
          ...m,
          open_alerts: mine.length,
          worst_severity: mine.reduce<SeverityLevel | null>((w, a) => (!w || SEVERITY_RANK[a.severity] > SEVERITY_RANK[w] ? a.severity : w), null),
        };
      }),
    };
  }, [res.data, alerts.data]);
  return { ...res, data };
}

export function useMaintenance(): Resource<MaintenancePrediction[]> {
  return useResource<MaintenancePrediction[]>(
    'maintenance',
    pick(
      () => mock('maintenance_predictions'),
      async () => api.maintenance((await api.machines(officeSite())).map((m) => m.machine_id)),
    ),
    [],
  );
}

export function useClusters(): Resource<Clusters | null> {
  return useResource<Clusters | null>(
    'clusters',
    pick(
      () => mock('clusters'),
      // PCA points only exist in ml/artifacts/clustering (no endpoint yet): metrics are live, the scatter is the artifact's.
      async () => ({ ...(await mock('clusters')), metrics: await api.fleetMetrics(officeSite()) }),
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

/** POST /chat in the cab's language. Mock: the cached answer whose question shares the most words, else "not in the manuals". */
export async function sendChat(operatorId: string, message: string, sessionId: string): Promise<ChatAnswer> {
  if (!USE_MOCKS) return api.chat({ session_id: sessionId, operator_id: operatorId, message, language: getLanguage() });
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

/**
 * The backend's own words when the chatbot is rate-limited (503 CHAT_BUSY, e.g. "Chatbot busy, try
 * again in a minute"); null for any other failure, which the chat shows as a generic error.
 */
export function chatBusyMessage(e: unknown): string | null {
  return e instanceof api.ApiError && e.status === 503 && e.code === 'CHAT_BUSY' ? e.message : null;
}

export async function chatSuggestions(): Promise<string[]> {
  if (!USE_MOCKS) return [t('chat.suggest1'), t('chat.suggest2'), t('chat.suggest3')];
  return (await mock('chat')).examples.map((e) => e.question);
}

export { getCached };
