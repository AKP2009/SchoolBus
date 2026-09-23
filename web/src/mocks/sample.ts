// Sample data for the styleguide and the layout demos until the screens are wired to Supabase.
// IDs follow CLAUDE.md rule 8.
import type { DigitalTwinProps } from '@/components/DigitalTwin';
import type { SafetyPanelProps } from '@/components/SafetyPanel';
import type { Alert, Task } from '@/types/domain';

const SHIFT = 'SH-2026-08-29-M04-D';

export const sampleTasks: Task[] = [
  {
    task_id: `T-${SHIFT}-1`,
    task_type: 'dig',
    material_type: 'clay',
    quantity: 180,
    unit: 'm3',
    predicted_p10_min: 35,
    predicted_p50_min: 42,
    predicted_p90_min: 55,
    prediction_factors: [
      { feature: 'rain_mm', label: 'Rain', impact_min: 8.1 },
      { feature: 'hours_into_shift', label: 'Late in shift', impact_min: 3.4 },
      { feature: 'operator_skill', label: 'Your experience', impact_min: -2.2 },
    ],
    status: 'in_progress',
  },
  {
    task_id: `T-${SHIFT}-2`,
    task_type: 'load',
    material_type: 'rock',
    quantity: 60,
    unit: 'tons',
    predicted_p10_min: 22,
    predicted_p50_min: 28,
    predicted_p90_min: 39,
    prediction_factors: [
      { feature: 'material_type', label: 'Rock', impact_min: 6.0 },
      { feature: 'haul_distance_m', label: 'Short haul', impact_min: -1.5 },
    ],
    status: 'scheduled',
  },
  {
    task_id: `T-${SHIFT}-3`,
    task_type: 'backfill',
    material_type: 'sand',
    quantity: 90,
    unit: 'm3',
    predicted_p10_min: 18,
    predicted_p50_min: 24,
    predicted_p90_min: 31,
    prediction_factors: null,
    status: 'completed',
  },
];

export const sampleWarning = {
  title: 'Hydraulic oil hot — 94 °C',
  instruction: 'Switch to economy mode and reduce load',
};

export const sampleTakeovers: Alert[] = [
  {
    id: 101,
    ts: '2026-08-29T06:12:00Z',
    machine_id: 'M04',
    title: 'Coolant temperature critical — 108 °C',
    message: 'Engine is overheating.',
    recommended_action: 'Lower the bucket, move to level ground, then shut down',
    severity: 'critical',
    stage: 'recommend_shutdown',
    steps: ['Lower the bucket to the ground', 'Move to level ground', 'Idle for 2 minutes, then shut down'],
  },
  {
    id: 102,
    ts: '2026-08-29T06:12:30Z',
    machine_id: 'M04',
    title: 'Person behind you — 2.4 m',
    message: 'Someone is in the rear danger zone.',
    recommended_action: 'Stop moving and check your rear camera',
    severity: 'critical',
    stage: 'warn',
  },
];

export const sampleEmergency: Alert = {
  id: 103,
  ts: '2026-08-29T06:14:00Z',
  machine_id: 'M04',
  title: 'SOS sent',
  message: 'Your location and machine state were sent to the site manager.',
  recommended_action: 'Stay in the cab if it is safe. Help is on the way.',
  severity: 'emergency',
  stage: 'escalated',
};

export const sampleSafety: SafetyPanelProps = {
  sectors: {
    front: { distance_m: null },
    rear: { distance_m: 2.4, approaching: true },
    left: { distance_m: 5.1 },
    right: { distance_m: null },
  },
  fatigue: 'medium',
  seatbeltFastened: true,
  pitchDeg: 17,
  rollDeg: 4,
};

export const sampleSafetyClear: SafetyPanelProps = {
  sectors: {
    front: { distance_m: null },
    rear: { distance_m: null },
    left: { distance_m: null },
    right: { distance_m: 9.5 },
  },
  fatigue: 'low',
  seatbeltFastened: true,
  pitchDeg: 3,
  rollDeg: 1,
};

const series = (base: number, drift: number, noise: number) =>
  Array.from({ length: 60 }, (_, i) => base + drift * i + noise * Math.sin(i * 1.7) * Math.cos(i * 0.6));

export const sampleTwin: DigitalTwinProps = {
  scores: {
    engine_score: 0.91,
    cooling_score: 0.62,
    hydraulics_score: 0.41,
    electrical_score: 0.88,
    undercarriage_score: null,
  },
  signals: {
    engine: [
      { label: 'Engine speed', unit: 'rpm', points: series(1650, 0, 60) },
      { label: 'Oil pressure', unit: 'kPa', points: series(410, -0.3, 8) },
    ],
    cooling: [{ label: 'Coolant temperature', unit: '°C', points: series(88, 0.25, 1.2) }],
    hydraulics: [
      { label: 'Hydraulic oil temperature', unit: '°C', points: series(72, 0.37, 1.5) },
      { label: 'Hydraulic pressure', unit: 'bar', points: series(265, 0.1, 9) },
    ],
    electrical: [{ label: 'Battery voltage', unit: 'V', points: series(27.6, -0.004, 0.08) }],
  },
};

export const sampleStatusBar = { machineId: 'M04', shiftMinutes: 252, fuelPct: 62 };
