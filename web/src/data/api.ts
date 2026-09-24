// Real data access (VITE_USE_MOCKS=false): Supabase under RLS for plain reads and writes, FastAPI for
// anything that needs a model, an LLM, the service role or the stream (docs/api_contract.md).
// Only web/src/data/hooks.ts calls these.
import { supabase } from '@/lib/supabase';
import type {
  AlertRow,
  ChatAnswer,
  FatigueRow,
  Geofence,
  IncidentRow,
  MachineHealth,
  MaintenancePrediction,
  PlanResult,
  ReplayStatus,
  SafetyEventRow,
  ShiftRow,
  TaskPrediction,
  TaskRow,
  TrainingModule,
  TrainingRecommendation,
  TrainingRecord,
  FleetMetricRow,
} from '@/types/domain';
import { API_URL } from './config';

function db() {
  if (!supabase) throw new Error('Supabase is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.');
  return supabase;
}

async function rows<T>(q: PromiseLike<{ data: unknown; error: { message: string } | null }>): Promise<T> {
  const { data, error } = await q;
  if (error) throw new Error(error.message);
  return data as T;
}

async function token(): Promise<string | null> {
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

/** FastAPI call; errors carry the contract's `error.message`. */
export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const t = await token();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    method: init.method ?? (init.json !== undefined ? 'POST' : 'GET'),
    headers: {
      ...(init.json !== undefined ? { 'content-type': 'application/json' } : {}),
      ...(t ? { authorization: `Bearer ${t}` } : {}),
      ...init.headers,
    },
    body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { error?: { message?: string } };
      msg = body.error?.message ?? msg;
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  return (await res.json()) as T;
}

// -- auth / profile ------------------------------------------------------------------------------
export async function signIn(email: string, password: string) {
  const { data, error } = await db().auth.signInWithPassword({ email, password });
  if (error) throw new Error(error.message);
  const profile = await rows<{ role: string; full_name: string | null; operator_id: string | null; site_id: string | null }>(
    db().from('profiles').select('role, full_name, operator_id, site_id').eq('id', data.user.id).single(),
  );
  return profile;
}

export const currentShift = (operatorId: string) =>
  rows<Array<ShiftRow & { site_id: string; fuel_start_pct: number | null }>>(
    db().from('shifts').select('*').eq('operator_id', operatorId).order('start_time', { ascending: false }).limit(1),
  ).then((r) => r[0] ?? null);

// -- operator ------------------------------------------------------------------------------------
export const tasks = (operatorId: string, taskDate: string) =>
  rows<TaskRow[]>(db().from('tasks').select('*').eq('operator_id', operatorId).eq('task_date', taskDate).order('sequence_no'));

export const updateTask = (taskId: string, patch: Partial<TaskRow>) =>
  rows<unknown>(db().from('tasks').update(patch).eq('task_id', taskId));

export const predictTaskTime = (taskIds: string[]) =>
  api<{ predictions: TaskPrediction[] }>('/predict/task-time', { json: { task_ids: taskIds } });

export const handoverShift = (machineId: string, before: string) =>
  rows<Array<ShiftRow & { handover_summary: string | null; handover_notes: string | null; issues_reported: string[] | null; fuel_start_pct: number | null; fuel_end_pct: number | null }>>(
    db().from('shifts').select('*').eq('machine_id', machineId).lt('end_time', before).order('start_time', { ascending: false }).limit(1),
  ).then((r) => r[0] ?? null);

export const generateHandover = (shiftId: string) => api<{ summary: string }>(`/handover/${encodeURIComponent(shiftId)}`, { method: 'POST' });

export const machineShifts = (machineId: string, since: string) =>
  rows<Array<ShiftRow & { handover_notes: string | null; issues_reported: string[] | null; fuel_start_pct: number | null; fuel_end_pct: number | null }>>(
    db().from('shifts').select('*').eq('machine_id', machineId).gte('start_time', since).order('start_time', { ascending: false }),
  );
export const machineAlerts = (machineId: string, since: string) =>
  rows<AlertRow[]>(db().from('alerts').select('*').eq('machine_id', machineId).gte('ts', since).order('ts', { ascending: false }));
export const machineIncidents = (machineId: string, since: string) =>
  rows<IncidentRow[]>(db().from('incidents').select('*').eq('machine_id', machineId).gte('ts', since));

export const fatigue = (shiftId: string) =>
  rows<FatigueRow[]>(db().from('fatigue_log').select('*').eq('shift_id', shiftId).order('ts'));

export const upsertIncident = (row: Partial<IncidentRow>) =>
  rows<unknown>(db().from('incidents').upsert(row, { onConflict: 'client_id' }));

export const upsertTrainingRecord = (row: Partial<TrainingRecord>) =>
  rows<unknown>(db().from('training_records').upsert(row, { onConflict: 'client_id' }));

export const training = async (operatorId: string) => ({
  modules: await rows<TrainingModule[]>(db().from('training_modules').select('*')),
  records: await rows<TrainingRecord[]>(db().from('training_records').select('*').eq('operator_id', operatorId)),
  recommendations: await rows<TrainingRecommendation[]>(
    db().from('training_recommendations').select('*').eq('operator_id', operatorId).eq('status', 'pending'),
  ),
});

export const chat = (body: { session_id: string; operator_id: string; message: string; language: string }) =>
  api<ChatAnswer>('/chat', { json: body });

export const reEvaluatePlan = (shiftId: string, reason: string) =>
  api<PlanResult>('/plan/re-evaluate', { json: { shift_id: shiftId, reason } });
export const acceptPlan = (shiftId: string) => api<{ shift_id: string; applied: boolean }>('/plan/accept', { json: { shift_id: shiftId } });

// -- alerts -------------------------------------------------------------------------------------
export const openAlerts = (siteId: string) => rows<AlertRow[]>(db().from('v_open_alerts').select('*').eq('site_id', siteId));

export async function acknowledgeAlert(id: number) {
  const { data } = await db().auth.getUser();
  return rows<unknown>(db().from('alerts').update({ acknowledged_by: data.user?.id ?? null, acknowledged_at: new Date().toISOString() }).eq('id', id));
}
export const resolveAlert = (id: number) =>
  rows<unknown>(db().from('alerts').update({ resolved_at: new Date().toISOString(), stage: 'resolved' }).eq('id', id));

/** Realtime inserts/updates on `alerts` for the site; returns an unsubscribe function. */
export function subscribeAlerts(siteId: string, onRow: (row: AlertRow) => void): () => void {
  const ch = db()
    .channel(`alerts-${siteId}`)
    .on('postgres_changes', { event: '*', schema: 'public', table: 'alerts', filter: `site_id=eq.${siteId}` }, (p) =>
      onRow(p.new as AlertRow),
    )
    .subscribe();
  return () => {
    void db().removeChannel(ch);
  };
}

// -- manager ------------------------------------------------------------------------------------
export const machines = (siteId: string) => rows<Array<Record<string, unknown>>>(db().from('machines').select('*').eq('site_id', siteId));
export const latestTelemetry = () => rows<Array<Record<string, unknown>>>(db().from('v_machine_latest').select('*'));
export const healthLatest = () => rows<Array<Record<string, unknown>>>(db().from('v_machine_health_latest').select('*'));
export const machineHealth = (id: string) => api<MachineHealth>(`/machine/${encodeURIComponent(id)}/health`);
export const geofences = (siteId: string) => rows<Geofence[]>(db().from('geofences').select('*').eq('site_id', siteId));
export const saveGeofence = (g: Omit<Geofence, 'id' | 'created_at' | 'created_by'>) =>
  rows<Geofence[]>(db().from('geofences').insert(g).select()).then((r) => r[0]!);
export const setGeofenceActive = (id: number, active: boolean) => rows<unknown>(db().from('geofences').update({ active }).eq('id', id));

export const maintenance = () =>
  rows<MaintenancePrediction[]>(db().from('maintenance_predictions').select('*').order('predicted_at', { ascending: false }).limit(200)).then(
    (all) => [...new Map(all.map((r) => [r.machine_id, r])).values()].sort((a, b) => b.failure_probability - a.failure_probability),
  );

export async function fleetMetrics(): Promise<FleetMetricRow[]> {
  const latest = await rows<Array<{ week_start: string }>>(
    db().from('fleet_metrics_weekly').select('week_start').order('week_start', { ascending: false }).limit(1),
  );
  const week = latest[0]?.week_start;
  return week ? rows<FleetMetricRow[]>(db().from('fleet_metrics_weekly').select('*').eq('week_start', week)) : [];
}
export async function verifyOutlier(id: number) {
  const { data } = await db().auth.getUser();
  return rows<unknown>(db().from('fleet_metrics_weekly').update({ verified_by: data.user?.id, verified_at: new Date().toISOString() }).eq('id', id));
}

export const safetyEvents = (siteId: string, since: string) =>
  rows<SafetyEventRow[]>(db().from('safety_events').select('*').eq('site_id', siteId).gte('ts', since).order('ts', { ascending: false }));
export const shifts = (siteId: string, since: string) =>
  rows<ShiftRow[]>(db().from('shifts').select('shift_id, operator_id, machine_id, shift_type, shift_date, start_time, end_time').eq('site_id', siteId).gte('start_time', since));
export const incidents = (siteId: string) =>
  rows<IncidentRow[]>(db().from('incidents').select('*').eq('site_id', siteId).order('ts', { ascending: false }).limit(100));

// -- demo --------------------------------------------------------------------------------------
export const replayStart = (body: { machine_ids: string[]; from: string; speed: number }) => api<ReplayStatus>('/replay/start', { json: body });
export const replayStop = () => api<ReplayStatus>('/replay/stop', { method: 'POST' });
export const replayStatus = () => api<ReplayStatus>('/replay/status');
export const scenario = (name: string, machineId: string) =>
  api<{ scenario: string; machine_id: string; started: boolean; start_ts: string; duration_min: number }>(`/scenario/${name}`, {
    json: { machine_id: machineId },
  });
export const postEvent = (body: Record<string, unknown>) => api<{ stored: boolean; alert_id: number }>('/events', { json: body });
