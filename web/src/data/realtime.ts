/**
 * Supabase Realtime (docs/supabase.md §6) for live mode: alerts, safety_events, incidents, tasks,
 * machine_health_snapshots and maintenance_predictions. The cab subscribes to its machine
 * (`machine_id=eq.M05`), the office to its site (`site_id=eq.S1`; the two tables without a site_id
 * use `machine_id=in.(…)` of the site's machines). Changes patch the loaded resources in place, so
 * screens re-render without a reload. Realtime applies RLS with the signed-in user's JWT.
 */
import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import type { RealtimePostgresChangesPayload } from '@supabase/supabase-js';
import { create } from 'zustand';
import { client, type App, type Db } from '@/lib/supabase';
import { useSession } from '@/stores/session';
import type { Tables } from '@/types/supabase';
import type { AlertRow, Fleet, IncidentRow, MachineHealth, MaintenancePrediction, SafetyEventRow, TaskRow } from '@/types/domain';
import * as api from './api';
import { useAuth } from './auth';
import { USE_MOCKS } from './config';
import { patchCached } from './resource';

type Table = 'alerts' | 'safety_events' | 'incidents' | 'tasks' | 'machine_health_snapshots' | 'maintenance_predictions';

/** Alert rows seen on Realtime this session (the feed merges them over v_open_alerts). */
export const useRealtimeAlerts = create<{ rows: Record<number, AlertRow> }>(() => ({ rows: {} }));

/** Realtime channel state, for the status bar and the demo panel. */
export const useRealtimeStatus = create<{ status: 'off' | 'connecting' | 'live' | 'error' }>(() => ({ status: 'off' }));

// -- patches (pure over the resource cache) -----------------------------------------------------
function byId<T extends { id: number }>(list: T[], row: T): T[] {
  const i = list.findIndex((r) => r.id === row.id);
  return i >= 0 ? list.map((r, k) => (k === i ? row : r)) : [row, ...list];
}

export function onAlert(row: AlertRow): void {
  useRealtimeAlerts.setState((s) => ({ rows: { ...s.rows, [row.id]: row } }));
}

export function onSafetyEvent(row: SafetyEventRow): void {
  patchCached<SafetyEventRow[]>(`safety:${row.site_id}`, (prev) => byId(prev, row).sort((a, b) => b.ts.localeCompare(a.ts)));
}

export function onIncident(row: IncidentRow): void {
  // The optimistic local copy has a negative id and the same client_id: replace it.
  patchCached<IncidentRow[]>(`incidents:${row.site_id}`, (prev) => {
    const i = prev.findIndex((r) => r.client_id === row.client_id || r.id === row.id);
    return i >= 0 ? prev.map((r, k) => (k === i ? row : r)) : [row, ...prev];
  });
}

export function onTask(row: TaskRow): void {
  patchCached<TaskRow[]>(`tasks:${row.shift_id}`, (prev) => prev.map((t) => (t.task_id === row.task_id ? row : t)));
}

export function onHealth(row: Tables<'machine_health_snapshots'>): void {
  const h = (v: number | null) => (v == null ? 1 : Number(v));
  const subsystems = {
    engine: h(row.engine_score),
    cooling: h(row.cooling_score),
    hydraulics: h(row.hydraulics_score),
    electrical: h(row.electrical_score),
    undercarriage: h(row.undercarriage_score),
  };
  const patch = { ts: row.ts, overall: Number(row.overall_score), subsystems, anomaly_score: row.anomaly_score };
  patchCached<MachineHealth[]>('health:all', (prev) => {
    const found = prev.some((m) => m.machine_id === row.machine_id);
    const next = prev.map((m) => (m.machine_id === row.machine_id ? { ...m, ...patch } : m));
    return found ? next : [...next, { machine_id: row.machine_id, failure_probability: null, likely_component: null, ...patch }];
  });
  patchCached<MachineHealth | null>(`health:${row.machine_id}`, (prev) => (prev ? { ...prev, ...patch } : prev));
  for (const key of fleetKeys) {
    patchCached<Fleet | null>(key, (f) =>
      f ? { ...f, machines: f.machines.map((m) => (m.machine_id === row.machine_id ? { ...m, health_overall: patch.overall } : m)) } : f,
    );
  }
}

export function onPrediction(row: MaintenancePrediction): void {
  patchCached<MaintenancePrediction[]>('maintenance', (prev) => api.latestPerMachine([...prev, row]));
}

/** Fleet resources loaded this session (health patches reach every site's map). */
export const fleetKeys = new Set<string>();

// -- channels -----------------------------------------------------------------------------------
interface Spec {
  table: Table;
  filter: string;
}

const HANDLERS: Record<Table, (row: never) => void> = {
  alerts: onAlert,
  safety_events: onSafetyEvent,
  incidents: onIncident,
  tasks: onTask,
  machine_health_snapshots: onHealth,
  maintenance_predictions: onPrediction,
};

function subscribe(db: Db, name: string, specs: Spec[]): () => void {
  let ch = db.channel(name);
  for (const { table, filter } of specs) {
    ch = ch.on('postgres_changes', { event: '*', schema: 'public', table, filter }, (p: RealtimePostgresChangesPayload<Record<string, unknown>>) => {
      if (p.eventType === 'DELETE') return; // nothing in the app deletes these rows
      (HANDLERS[table] as (row: unknown) => void)(p.new);
    });
  }
  useRealtimeStatus.setState({ status: 'connecting' });
  ch.subscribe((status) => {
    if (status === 'SUBSCRIBED') useRealtimeStatus.setState({ status: 'live' });
    else if (status === 'CHANNEL_ERROR' || status === 'TIMED_OUT') useRealtimeStatus.setState({ status: 'error' }); // the client rejoins by itself
  });
  return () => {
    useRealtimeStatus.setState({ status: 'off' });
    void db.removeChannel(ch);
  };
}

const TABLES: Table[] = ['alerts', 'safety_events', 'incidents', 'tasks', 'machine_health_snapshots', 'maintenance_predictions'];

export function cabSpecs(machineId: string): Spec[] {
  return TABLES.map((table) => ({ table, filter: `machine_id=eq.${machineId}` }));
}

export function siteSpecs(siteId: string, machineIds: string[]): Spec[] {
  const inSite = `machine_id=in.(${machineIds.join(',')})`;
  const byMachine = (t: Table) => t === 'machine_health_snapshots' || t === 'maintenance_predictions';
  return TABLES.filter((t) => !byMachine(t) || machineIds.length > 0).map((table) => ({
    table,
    filter: byMachine(table) ? inSite : `site_id=eq.${siteId}`,
  }));
}

/** Opens this tab's Realtime channel once signed in. Mount once (App); no-op with mocks. */
export function useRealtimeSync(): void {
  const { pathname } = useLocation();
  const app: App = pathname.startsWith('/operator') ? 'cab' : 'office';
  const slot = useAuth((s) => s[app]);
  const cabMachine = useSession((s) => (app === 'cab' ? (s.cab?.machineId ?? null) : null));
  const siteId = app === 'office' && slot.profile && slot.profile.role !== 'operator' ? (slot.profile.site_id ?? 'S1') : null;
  const [siteMachines, setSiteMachines] = useState<string[] | null>(null);
  const signedIn = !USE_MOCKS && slot.status === 'signed_in';

  useEffect(() => {
    setSiteMachines(null);
    if (!signedIn || !siteId) return;
    let alive = true;
    void api.machines(siteId).then(
      (ms) => alive && setSiteMachines(ms.map((m) => m.machine_id)),
      () => alive && setSiteMachines([]),
    );
    return () => {
      alive = false;
    };
  }, [signedIn, siteId]);

  useEffect(() => {
    if (!signedIn) return;
    const db = client(app);
    if (!db) return;
    if (app === 'cab' && cabMachine) return subscribe(db, `cab-${cabMachine}`, cabSpecs(cabMachine));
    if (app === 'office' && siteId && siteMachines) return subscribe(db, `site-${siteId}`, siteSpecs(siteId, siteMachines));
  }, [signedIn, app, cabMachine, siteId, siteMachines]);
}
