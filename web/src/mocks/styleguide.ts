// Styleguide samples, taken from the generated mocks (scripts/build_mocks.py) instead of hand-written
// values. Screens never import this: they go through web/src/data/hooks.ts.
import type { DigitalTwinProps } from '@/components/DigitalTwin';
import type { SafetyPanelProps } from '@/components/SafetyPanel';
import { stepsFor } from '@/lib/alertSteps';
import type { Alert, AlertRow, MachineHealth, MachineSignals, SafetyEventRow, Task, Telemetry, World } from '@/types/domain';
import alertsJson from './alerts_open.json';
import healthJson from './machine_health.json';
import liveJson from './machine_live.json';
import signalsJson from './machine_signals.json';
import safetyJson from './safety_events.json';
import tasksJson from './tasks.json';
import worldJson from './world.json';

const world = worldJson as unknown as World;
const alerts = alertsJson as unknown as AlertRow[];
const health = healthJson as unknown as MachineHealth[];
const live = liveJson as unknown as Telemetry;
const signals = signalsJson as unknown as MachineSignals;
const safety = safetyJson as unknown as SafetyEventRow[];

export const sampleTasks: Task[] = (tasksJson as unknown as Task[]).slice(2, 5);

const warning = alerts.find((a) => a.severity === 'warning');
export const sampleWarning = {
  title: warning?.title ?? 'Engine coolant hot — 102 °C',
  instruction: warning?.recommended_action ?? 'Reduce load and let the engine cool.',
};

export const sampleTakeovers: Alert[] = alerts
  .filter((a) => a.severity === 'critical')
  .map((a) => ({ ...a, steps: stepsFor(a.alert_code, a.stage, world.machine.machine_type) }));

export const sampleEmergency: Alert = {
  id: 103,
  ts: world.now,
  machine_id: world.machine.machine_id,
  title: 'SOS sent',
  message: 'Your location and machine state were sent to the site manager.',
  recommended_action: 'Stay in the cab if it is safe. Help is on the way.',
  severity: 'emergency',
  stage: 'escalated',
};

const near = safety.find((e) => e.distance_m != null && e.sector && e.sector !== 'cab');
export const sampleSafety: SafetyPanelProps = {
  sectors: {
    front: { distance_m: null },
    rear: { distance_m: null },
    left: { distance_m: 5.1 },
    right: { distance_m: null },
    ...(near ? { [near.sector as 'rear']: { distance_m: near.distance_m, approaching: !!near.approaching } } : {}),
  },
  fatigue: 'medium',
  seatbeltFastened: true,
  pitchDeg: 17,
  rollDeg: live.roll_deg ?? 1,
};

export const sampleSafetyClear: SafetyPanelProps = {
  sectors: { front: { distance_m: null }, rear: { distance_m: null }, left: { distance_m: null }, right: { distance_m: 9.5 } },
  fatigue: 'low',
  seatbeltFastened: true,
  pitchDeg: live.pitch_deg ?? 1,
  rollDeg: live.roll_deg ?? 1,
};

const h = health.find((x) => x.machine_id === world.machine.machine_id) ?? health[0]!;
const pts = (k: keyof Telemetry) => (signals.series[k] ?? []).filter((v): v is number => typeof v === 'number');
export const sampleTwin: DigitalTwinProps = {
  scores: {
    engine_score: h.subsystems.engine,
    cooling_score: h.subsystems.cooling,
    hydraulics_score: h.subsystems.hydraulics,
    electrical_score: h.subsystems.electrical,
    undercarriage_score: null,
  },
  signals: {
    engine: [
      { label: 'Engine speed', unit: 'rpm', points: pts('engine_rpm') },
      { label: 'Oil pressure', unit: 'kPa', points: pts('oil_pressure_kpa') },
    ],
    cooling: [{ label: 'Coolant temperature', unit: '°C', points: pts('coolant_temp_c') }],
    hydraulics: [
      { label: 'Hydraulic oil temperature', unit: '°C', points: pts('hydraulic_oil_temp_c') },
      { label: 'Hydraulic pressure', unit: 'bar', points: pts('hydraulic_pressure_bar') },
    ],
    electrical: [{ label: 'Battery voltage', unit: 'V', points: pts('battery_voltage') }],
  },
};

export const sampleStatusBar = {
  machineId: world.machine.machine_id,
  shiftMinutes: Math.round((Date.parse(world.now) - Date.parse(world.shift.start_time)) / 60000),
  fuelPct: Math.round(live.fuel_level_pct ?? world.shift.fuel_start_pct),
};
