// Real data access (VITE_USE_MOCKS=false): Supabase under RLS for plain reads and writes, FastAPI for
// anything that needs a model, an LLM, the service role or the stream (docs/api_contract.md).
// Only web/src/data/ calls these. Queries use the typed client (web/src/types/supabase.ts); rows whose
// jsonb columns have a known shape are narrowed to the domain types.
import { client, type Db } from '@/lib/supabase';
import type { Json, TablesInsert, TablesUpdate } from '@/types/supabase';
import type {
  AlertRow,
  ChatAnswer,
  FatigueRow,
  FleetMetricRow,
  Geofence,
  IncidentRow,
  MachineHealth,
  MaintenancePrediction,
  PlanResult,
  ReplayStatus,
  SafetyEventRow,
  ShiftFull,
  ShiftRow,
  TaskPrediction,
  TaskRow,
  TrainingModule,
  TrainingRecommendation,
  TrainingRecord,
} from '@/types/domain';
import { accessToken, refreshToken } from './auth';
import { API_URL } from './config';

export function db(): Db {
  const c = client();
  if (!c) throw new Error('Supabase is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.');
  return c;
}

type Result = { data: unknown; error: { message: string } | null };

/** The rows of a list or `.single()` query (throws on error). */
async function rows<Q extends PromiseLike<Result>>(q: Q): Promise<NonNullable<Awaited<Q>['data']>> {
  const { data, error } = await q;
  if (error) throw new Error(error.message);
  return data as NonNullable<Awaited<Q>['data']>;
}

/** The row of a `.maybeSingle()` query, or null. */
async function maybe<Q extends PromiseLike<Result>>(q: Q): Promise<Awaited<Q>['data']> {
  const { data, error } = await q;
  if (error) throw new Error(error.message);
  return data;
}

async function ok(q: PromiseLike<{ error: { message: string; code?: string } | null }>): Promise<void> {
  const { error } = await q;
  if (error) throw Object.assign(new Error(error.message), { code: error.code });
}

/** A FastAPI error in the contract's shape. `status` 0 = the backend couldn't be reached. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null,
  ) {
    super(message);
  }
}

/** FastAPI call with the signed-in user's Supabase JWT; one refresh-and-retry on 401. */
export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const call = async (t: string | null) => {
    try {
      return await fetch(`${API_URL}${path}`, {
        ...init,
        method: init.method ?? (init.json !== undefined ? 'POST' : 'GET'),
        headers: {
          ...(init.json !== undefined ? { 'content-type': 'application/json' } : {}),
          ...(t ? { authorization: `Bearer ${t}` } : {}),
          ...init.headers,
        },
        body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
      });
    } catch {
      throw new ApiError('The backend is not reachable.', 0, null);
    }
  };
  let res = await call(await accessToken());
  if (res.status === 401) {
    const t = await refreshToken();
    if (t) res = await call(t);
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    let code: string | null = null;
    try {
      const body = (await res.json()) as { error?: { message?: string; code?: string } };
      msg = body.error?.message ?? msg;
      code = body.error?.code ?? null;
    } catch {
      /* not JSON */
    }
    throw new ApiError(msg, res.status, code);
  }
  return (await res.json()) as T;
}

// -- operator ------------------------------------------------------------------------------------
/** The operator's shift at data time `at`: the one running then, else the last one that started before. */
export async function shiftAt(operatorId: string, at: string): Promise<ShiftFull | null> {
  const started = await rows(
    db().from('shifts').select('*').eq('operator_id', operatorId).lte('start_time', at).order('start_time', { ascending: false }).limit(1),
  );
  if (started[0]) return started[0];
  const any = await rows(db().from('shifts').select('*').eq('operator_id', operatorId).order('start_time', { ascending: false }).limit(1));
  return any[0] ?? null;
}

export const operator = (operatorId: string) => maybe(db().from('operators').select('*').eq('operator_id', operatorId).maybeSingle());
export const machine = (machineId: string) => rows(db().from('machines').select('*').eq('machine_id', machineId).single());
export const operatorName = (operatorId: string) =>
  maybe(db().from('operators').select('full_name').eq('operator_id', operatorId).maybeSingle()).then((r) => r?.full_name ?? null);

/** The shift's tasks for this operator in order (docs/api_contract.md "Today's tasks", keyed by the cab's shift). */
export const tasks = (operatorId: string, shiftId: string) =>
  rows(db().from('tasks').select('*').eq('operator_id', operatorId).eq('shift_id', shiftId).order('sequence_no')) as Promise<unknown> as Promise<TaskRow[]>;

export const updateTask = (taskId: string, patch: TablesUpdate<'tasks'>) => ok(db().from('tasks').update(patch).eq('task_id', taskId));

export const predictTaskTime = (taskIds: string[]) => api<{ predictions: TaskPrediction[] }>('/predict/task-time', { json: { task_ids: taskIds } });

/** The machine's last shift that ended by `before`. */
export const handoverShift = (machineId: string, before: string) =>
  rows(db().from('shifts').select('*').eq('machine_id', machineId).lte('end_time', before).order('start_time', { ascending: false }).limit(1)).then(
    (r) => r[0] ?? null,
  );
export const unfinishedTasks = (shiftId: string) =>
  rows(
    db()
      .from('tasks')
      .select('task_id, sequence_no, task_type, material_type, quantity, unit, status')
      .eq('shift_id', shiftId)
      .neq('status', 'completed')
      .order('sequence_no'),
  );

export const generateHandover = (shiftId: string) => api<{ summary: string }>(`/handover/${encodeURIComponent(shiftId)}`, { method: 'POST' });

export const machineShifts = (machineId: string, since: string, until: string) =>
  rows(db().from('shifts').select('*').eq('machine_id', machineId).gte('start_time', since).lte('start_time', until).order('start_time', { ascending: false }));
export const machineAlerts = (machineId: string, since: string, until: string) =>
  rows(db().from('alerts').select('*').eq('machine_id', machineId).gte('ts', since).lte('ts', until).order('ts', { ascending: false })) as Promise<
    unknown
  > as Promise<AlertRow[]>;
export const machineIncidents = (machineId: string, since: string, until: string) =>
  rows(db().from('incidents').select('*').eq('machine_id', machineId).gte('ts', since).lte('ts', until));
export const machineSafetyEvents = (machineId: string, since: string, until: string) =>
  rows(db().from('safety_events').select('ts, event_type').eq('machine_id', machineId).gte('ts', since).lte('ts', until));
export const shiftTaskStatus = (shiftIds: string[]) =>
  shiftIds.length ? rows(db().from('tasks').select('shift_id, status').in('shift_id', shiftIds)) : Promise.resolve([]);

export const fatigue = (shiftId: string) => rows(db().from('fatigue_log').select('*').eq('shift_id', shiftId).order('ts')) as Promise<FatigueRow[]>;

/**
 * Offline-queue writes, idempotent by client_id (CLAUDE.md rule 7). Incidents are append-only and
 * operators may not update them (RLS `inc_update` is managers only), so a retried incident is
 * INSERT … ON CONFLICT (client_id) DO NOTHING rather than DO UPDATE.
 */
export const upsertIncident = (row: TablesInsert<'incidents'>) =>
  ok(db().from('incidents').upsert(row, { onConflict: 'client_id', ignoreDuplicates: true }));
export const upsertTrainingRecord = (row: TablesInsert<'training_records'>) =>
  ok(db().from('training_records').upsert(row, { onConflict: 'client_id' }));

export const training = async (operatorId: string) => {
  const [modules, records, recommendations] = await Promise.all([
    rows(db().from('training_modules').select('*')),
    rows(db().from('training_records').select('*').eq('operator_id', operatorId).order('started_at', { ascending: false })),
    rows(db().from('training_recommendations').select('*').eq('operator_id', operatorId).eq('status', 'pending')),
  ]);
  return {
    modules: modules as unknown as TrainingModule[],
    records: records as TrainingRecord[],
    recommendations: recommendations as TrainingRecommendation[],
  };
};

export const chat = (body: { session_id: string; operator_id: string; message: string; language: string }) => api<ChatAnswer>('/chat', { json: body });

export const reEvaluatePlan = (shiftId: string, reason: string) => api<PlanResult>('/plan/re-evaluate', { json: { shift_id: shiftId, reason } });
export const acceptPlan = (shiftId: string) => api<{ shift_id: string; applied: boolean }>('/plan/accept', { json: { shift_id: shiftId } });

// -- alerts -------------------------------------------------------------------------------------
export const openAlerts = (siteId: string) =>
  rows(db().from('v_open_alerts').select('*').eq('site_id', siteId)) as Promise<unknown> as Promise<AlertRow[]>;
export const openMachineAlerts = (machineId: string) =>
  rows(db().from('v_open_alerts').select('*').eq('machine_id', machineId)) as Promise<unknown> as Promise<AlertRow[]>;

export async function acknowledgeAlert(id: number) {
  const { data } = await db().auth.getSession();
  return ok(db().from('alerts').update({ acknowledged_by: data.session?.user.id ?? null, acknowledged_at: new Date().toISOString() }).eq('id', id));
}
export const resolveAlert = (id: number) => ok(db().from('alerts').update({ resolved_at: new Date().toISOString(), stage: 'resolved' }).eq('id', id));

// -- manager ------------------------------------------------------------------------------------
export const sites = () => rows(db().from('sites').select('*').order('site_id'));
export const weatherAt = (siteId: string, at: string) =>
  rows(db().from('weather').select('*').eq('site_id', siteId).lte('ts', at).order('ts', { ascending: false }).limit(1)).then((r) => r[0] ?? null);
export const machines = (siteId: string) => rows(db().from('machines').select('*').eq('site_id', siteId).order('machine_id'));
const nameMap = (r: Array<{ operator_id: string; full_name: string }>) => new Map(r.map((o) => [o.operator_id, o.full_name]));
export const operatorNamesAtSite = (siteId: string) => rows(db().from('operators').select('operator_id, full_name').eq('site_id', siteId)).then(nameMap);
export const operatorNames = (ids: string[]) =>
  ids.length ? rows(db().from('operators').select('operator_id, full_name').in('operator_id', ids)).then(nameMap) : Promise.resolve(new Map<string, string>());

/** Each machine's last telemetry row at or before data time `at` (the replay doesn't write telemetry). */
export const telemetryAt = (machineIds: string[], at: string) =>
  Promise.all(
    machineIds.map((id) =>
      rows(
        db()
          .from('telemetry')
          .select('ts, machine_id, operator_id, shift_id, gps_lat, gps_lon, fuel_level_pct, ground_speed_kmh, is_idle')
          .eq('machine_id', id)
          .lte('ts', at)
          .order('ts', { ascending: false })
          .limit(1),
      ).then((r) => r[0] ?? null),
    ),
  );
export const healthLatest = () => rows(db().from('v_machine_health_latest').select('*'));
export const machineHealth = (id: string) => api<MachineHealth>(`/machine/${encodeURIComponent(id)}/health`);
export const geofences = (siteId: string) => rows(db().from('geofences').select('*').eq('site_id', siteId)) as Promise<unknown> as Promise<Geofence[]>;
export const saveGeofence = (g: Omit<Geofence, 'id' | 'created_at' | 'created_by'>) =>
  rows(db().from('geofences').insert({ ...g, polygon: g.polygon as unknown as Json }).select()).then((r) => r[0] as unknown as Geofence);
export const setGeofenceActive = (id: number, active: boolean) => ok(db().from('geofences').update({ active }).eq('id', id));

export const maintenance = (machineIds: string[]) =>
  rows(
    db().from('maintenance_predictions').select('*').in('machine_id', machineIds).order('predicted_at', { ascending: false }).limit(500),
  ).then((all) => latestPerMachine(all as unknown as MaintenancePrediction[]));

export function latestPerMachine(all: MaintenancePrediction[]): MaintenancePrediction[] {
  const best = new Map<string, MaintenancePrediction>();
  for (const r of all) {
    const b = best.get(r.machine_id);
    if (!b || r.predicted_at > b.predicted_at) best.set(r.machine_id, r);
  }
  return [...best.values()].sort((a, b) => b.failure_probability - a.failure_probability);
}

export async function fleetMetrics(siteId: string): Promise<FleetMetricRow[]> {
  const latest = await rows(db().from('fleet_metrics_weekly').select('week_start').eq('site_id', siteId).order('week_start', { ascending: false }).limit(1));
  const week = latest[0]?.week_start;
  return week ? rows(db().from('fleet_metrics_weekly').select('*').eq('site_id', siteId).eq('week_start', week)) : [];
}
export async function verifyOutlier(id: number) {
  const { data } = await db().auth.getSession();
  return ok(db().from('fleet_metrics_weekly').update({ verified_by: data.session?.user.id ?? null, verified_at: new Date().toISOString() }).eq('id', id));
}

export const safetyEvents = (siteId: string, since: string, until: string) =>
  rows(db().from('safety_events').select('*').eq('site_id', siteId).gte('ts', since).lte('ts', until).order('ts', { ascending: false })) as Promise<
    unknown
  > as Promise<SafetyEventRow[]>;
export const shifts = (siteId: string, since: string, until: string): Promise<ShiftRow[]> =>
  rows(
    db()
      .from('shifts')
      .select('shift_id, operator_id, machine_id, shift_type, shift_date, start_time, end_time')
      .eq('site_id', siteId)
      .gte('start_time', since)
      .lte('start_time', until),
  );
export const incidents = (siteId: string): Promise<IncidentRow[]> =>
  rows(db().from('incidents').select('*').eq('site_id', siteId).order('ts', { ascending: false }).limit(100));

// -- demo --------------------------------------------------------------------------------------
export const replayStart = (body: { machine_ids: string[]; from: string; speed: number }) => api<ReplayStatus>('/replay/start', { json: body });
export const replayStop = () => api<ReplayStatus>('/replay/stop', { method: 'POST' });
export const replayStatus = () => api<ReplayStatus>('/replay/status');
export const scenario = (name: string, machineId: string) =>
  api<{ scenario: string; machine_id: string; started: boolean; start_ts: string; duration_min: number }>(`/scenario/${name}`, {
    json: { machine_id: machineId },
  });
export const postEvent = (body: Record<string, unknown>) => api<{ stored: boolean; alert_id: number | null }>('/events', { json: body });
