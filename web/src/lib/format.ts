// Display helpers. Data is UTC; the UI shows Asia/Kolkata (CLAUDE.md conventions).
import type { IncidentType, MachineType, SafetyEventType, TaskType } from '@/types/domain';

const TZ = 'Asia/Kolkata';

const timeFmt = new Intl.DateTimeFormat('en-IN', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false });
const dateFmt = new Intl.DateTimeFormat('en-IN', { timeZone: TZ, day: 'numeric', month: 'short' });
const dayFmt = new Intl.DateTimeFormat('en-IN', { timeZone: TZ, weekday: 'short', day: 'numeric', month: 'short' });

export function fmtTime(iso: string | number | null | undefined): string {
  if (iso == null) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : timeFmt.format(d);
}

export function fmtDate(iso: string | number | null | undefined): string {
  if (iso == null) return '—';
  return dateFmt.format(new Date(iso));
}

export function fmtDay(iso: string | number | null | undefined): string {
  if (iso == null) return '—';
  return dayFmt.format(new Date(iso));
}

export function fmtDateTime(iso: string | number | null | undefined): string {
  if (iso == null) return '—';
  return `${fmtDate(iso)}, ${fmtTime(iso)}`;
}

/** "12 min ago" / "2 h ago" relative to `now` (ms). */
export function ago(iso: string | number, now: number): string {
  const min = Math.round((now - new Date(iso).getTime()) / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min} min ago`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} h ago`;
  return `${Math.floor(h / 24)} d ago`;
}

export function minutes(min: number | null | undefined): string {
  if (min == null) return '—';
  const m = Math.round(min);
  return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min` : `${m} min`;
}

export function pct(v: number | null | undefined, digits = 0): string {
  return v == null ? '—' : `${(v * 100).toFixed(digits)}%`;
}

export const TASK_WORD: Record<TaskType, string> = {
  dig: 'Dig',
  trench: 'Trench',
  load: 'Load',
  haul: 'Haul',
  grade: 'Grade',
  backfill: 'Backfill',
};

export const MACHINE_WORD: Record<MachineType, string> = {
  excavator: 'Excavator',
  wheel_loader: 'Wheel loader',
  dozer: 'Dozer',
  articulated_truck: 'Articulated truck',
};

export const INCIDENT_WORD: Record<IncidentType, string> = {
  near_miss: 'Near miss',
  collision: 'Collision',
  injury: 'Injury',
  equipment_damage: 'Equipment damage',
  spill_leak: 'Spill or leak',
  other: 'Other',
};

export const SAFETY_WORD: Record<SafetyEventType, string> = {
  seatbelt_unfastened: 'Seatbelt',
  proximity_breach: 'Person nearby',
  blindspot_intrusion: 'Blind spot',
  fatigue_high: 'Fatigue high',
  phone_use: 'Phone use',
  tip_risk: 'Tip risk',
  geofence_breach: 'Zone entered',
  harsh_maneuver: 'Harsh movement',
  overspeed: 'Too fast',
  sos: 'SOS',
};

export const COMPONENT_WORD: Record<string, string> = {
  engine: 'Engine',
  hydraulics: 'Hydraulics',
  cooling: 'Cooling',
  electrical: 'Electrical',
  brakes: 'Brakes',
  undercarriage: 'Undercarriage',
  other: 'Other',
};

/** Handover issue tags → words. */
export const ISSUE_WORD: Record<string, string> = {
  unfinished_task: 'Unfinished task',
  hydraulic_temp_high: 'Hydraulic oil ran hot',
  coolant_high: 'Coolant ran hot',
  people_near_machine: 'People near the machine',
  bucket_teeth_worn: 'Bucket teeth worn',
  breakdown: 'Breakdown',
};

export function capitalise(s: string): string {
  return s ? s[0]!.toUpperCase() + s.slice(1).replace(/_/g, ' ') : s;
}
