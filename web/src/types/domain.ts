// Table rows come from the generated web/src/types/supabase.ts (the migration is the source of truth).
// This file keeps only what differs from a plain row: jsonb columns narrowed to their real shape,
// UI subsets, and the FastAPI / WebSocket / mock shapes of docs/api_contract.md.
import type { Enums, Tables } from './supabase';

export type SeverityLevel = Enums<'severity_level'>;
export type AlertStage = Enums<'alert_stage'>;
export type TaskType = Enums<'task_type'>;
export type MaterialType = Enums<'material_type'>;
export type TaskStatus = Enums<'task_status'>;
export type CameraSector = Enums<'camera_sector'>;
export type FatigueLevel = Enums<'fatigue_level'>;
export type MachineType = Enums<'machine_type'>;
export type MachineStatus = Enums<'machine_status'>;
export type ShiftType = Enums<'shift_type'>;
export type AlertSource = Enums<'alert_source'>;
export type AlertCategory = Enums<'alert_category'>;
export type SafetyEventType = Enums<'safety_event_type'>;
export type MachineComponent = Enums<'machine_component'>;
export type IncidentType = Enums<'incident_type'>;
export type IncidentStatus = Enums<'incident_status'>;
export type ReportChannel = Enums<'report_channel'>;
export type GeofenceType = Enums<'geofence_type'>;
export type TrainingFormat = Enums<'training_format'>;
export type RecommendationStatus = Enums<'recommendation_status'>;
export type UserRole = Enums<'user_role'>;
export type Band = 'green' | 'orange' | 'red';
export type Subsystem = 'engine' | 'cooling' | 'hydraulics' | 'electrical' | 'undercarriage';

/** One entry of `tasks.prediction_factors` (docs/api_contract.md). */
export interface PredictionFactor {
  feature: string;
  label: string;
  impact_min: number;
}

/** A `tasks` row; jsonb `prediction_factors` and the checked `unit` / `priority` narrowed. */
export type TaskRow = Omit<Tables<'tasks'>, 'unit' | 'priority' | 'prediction_factors'> & {
  unit: 'm3' | 'tons';
  priority: 1 | 2 | 3;
  prediction_factors: PredictionFactor[] | null;
};

/** The fields TaskCard needs. */
export type Task = Pick<
  TaskRow,
  'task_id' | 'task_type' | 'material_type' | 'quantity' | 'unit' | 'predicted_p10_min' | 'predicted_p50_min' | 'predicted_p90_min' | 'prediction_factors' | 'status'
>;

/** POST /predict/task-time → predictions[] */
export interface TaskPrediction {
  task_id: string;
  p10_min: number;
  p50_min: number;
  p90_min: number;
  factors: PredictionFactor[];
  operator_avg_min: number | null;
  expected_efficiency: number | null;
}

/** The subset of an `alerts` row the alert components need. */
export interface Alert {
  id: number;
  ts: string;
  machine_id: string | null;
  title: string;
  message: string;
  recommended_action: string | null;
  severity: SeverityLevel;
  stage: AlertStage;
  /** UI-side steps for a critical takeover ("Lower the bucket", "Move to level ground", ...). */
  steps?: string[];
}

/** An `alerts` / `v_open_alerts` row (jsonb `evidence` as an object). */
export type AlertRow = Omit<Tables<'alerts'>, 'evidence'> & { evidence: Record<string, unknown> | null; steps?: string[] };

/** Subsystem scores from `machine_health_snapshots`, 0–1, null when unknown. */
export type HealthScores = Pick<Tables<'machine_health_snapshots'>, 'engine_score' | 'cooling_score' | 'hydraulics_score' | 'electrical_score' | 'undercarriage_score'>;

export interface HealthReason {
  subsystem: Subsystem;
  source: 'rule' | 'anomaly' | 'failure_probability';
  penalty: number;
  text: string;
}

/** GET /machine/{id}/health */
export interface MachineHealth {
  machine_id: string;
  ts: string;
  overall: number;
  subsystems: Record<Subsystem, number>;
  anomaly_score: number | null;
  failure_probability: number | null;
  likely_component: MachineComponent | null;
  band?: Band;
  subsystem_bands?: Record<Subsystem, Band>;
  reasons?: HealthReason[];
}

/** A `telemetry` row as streamed (never anomaly_label / anomaly_type, CLAUDE.md rule 1). */
export type Telemetry = Omit<Tables<'telemetry'>, 'id' | 'anomaly_label' | 'anomaly_type'>;

// -- WebSocket /stream/{machine_id} ------------------------------------------------------------
export interface StreamAlert {
  id: number;
  alert_code: string;
  severity: SeverityLevel;
  stage: AlertStage;
  title: string;
  recommended_action: string | null;
}
export interface StreamSafety {
  type: SafetyEventType;
  severity?: SeverityLevel;
  distance_m: number | null;
  sector: CameraSector | null;
  approaching: boolean | null;
}
export interface StreamFatigue {
  fatigue_level: FatigueLevel;
  fatigue_score: number;
}
export interface StreamHealth {
  overall: number;
  subsystems: Record<Subsystem, number>;
}
export type StreamMessage =
  | { kind: 'telemetry'; data: Telemetry }
  | { kind: 'alert'; data: StreamAlert }
  | { kind: 'safety'; data: StreamSafety }
  | { kind: 'health'; data: StreamHealth }
  | { kind: 'fatigue'; data: StreamFatigue };

export interface TelemetryStream {
  machine_id: string;
  from: string;
  to: string;
  messages: Array<StreamMessage & { at_s: number }>;
}

// -- People, machines, shifts ------------------------------------------------------------------
export type Site = Pick<Tables<'sites'>, 'site_id' | 'name'> & Partial<Pick<Tables<'sites'>, 'site_type' | 'lat' | 'lon' | 'timezone'>>;

export interface World {
  now: string;
  stream_from: string;
  stream_minutes: number;
  site: Site;
  sites: Site[];
  operator: {
    operator_id: string;
    full_name: string;
    experience_years: number;
    certification_level: number;
    languages: string[];
    preferred_shift: ShiftType;
  };
  manager: { name: string; role: string; site_id: string };
  machine: { machine_id: string; machine_type: MachineType; model: string; serial_no: string; year: number };
  shift: {
    shift_id: string;
    shift_type: ShiftType;
    shift_date: string;
    start_time: string;
    end_time: string;
    fuel_start_pct: number;
  };
  weather: {
    ts: string;
    temp_c: number;
    humidity_pct: number;
    rain_mm: number;
    wind_kmh: number;
    visibility_m: number;
    dust_index: number;
  };
}

/** The `shifts` columns the site views select. */
export type ShiftRow = Pick<Tables<'shifts'>, 'shift_id' | 'operator_id' | 'machine_id' | 'shift_type' | 'shift_date' | 'start_time' | 'end_time'>;
export type ShiftFull = Tables<'shifts'>;

export interface LogAlert {
  alert_code: string;
  title: string;
  severity: SeverityLevel;
  max_stage: AlertStage;
  ts: string;
  resolved_at: string | null;
}

/** Previous shift on this machine + its handover brief (models.md §9). */
export interface Handover {
  shift_id: string;
  machine_id: string;
  operator_id: string;
  operator_name: string | null;
  shift_type: ShiftType;
  start_time: string;
  end_time: string;
  fuel_start_pct: number | null;
  fuel_end_pct: number | null;
  handover_notes: string | null;
  issues_reported: string[];
  handover_summary: string | null;
  handover_generated_at: string | null;
  unfinished_tasks: Array<Pick<TaskRow, 'task_id' | 'sequence_no' | 'task_type' | 'material_type' | 'quantity' | 'unit' | 'status'>>;
  alerts: LogAlert[];
  safety_event_count: number;
}

export interface MachineLogShift {
  shift_id: string;
  shift_type: ShiftType;
  operator_id: string;
  operator_name: string | null;
  start_time: string;
  end_time: string;
  in_progress: boolean;
  fuel_start_pct: number | null;
  fuel_end_pct: number | null;
  handover_notes: string | null;
  issues_reported: string[];
  tasks_completed: number;
  tasks_total: number;
  alerts: LogAlert[];
  safety_events: Partial<Record<SafetyEventType, number>>;
  incidents: Array<Pick<IncidentRow, 'id' | 'ts' | 'incident_type' | 'severity' | 'description' | 'status'>>;
}
export interface MachineLogs {
  machine_id: string;
  from: string;
  to: string;
  shifts: MachineLogShift[];
}

/** A `safety_events` row (jsonb `details` as an object). */
export type SafetyEventRow = Omit<Tables<'safety_events'>, 'details'> & { details: Record<string, unknown> | null };

/** A `fatigue_log` row (mocks leave out `id`). */
export type FatigueRow = Omit<Tables<'fatigue_log'>, 'id'> & { id?: number };

export type IncidentRow = Tables<'incidents'>;

export interface GeoPolygon {
  type: 'Polygon';
  /** GeoJSON: [ring][point][lon, lat] */
  coordinates: number[][][];
}

/** A `geofences` row (jsonb `polygon` as GeoJSON). */
export type Geofence = Omit<Tables<'geofences'>, 'polygon'> & { polygon: GeoPolygon };

export interface FleetMachine {
  machine_id: string;
  site_id: string;
  machine_type: MachineType;
  model: string;
  status: MachineStatus;
  total_engine_hours: number;
  hours_since_service: number;
  service_interval_hours: number;
  live: {
    ts: string | null;
    on_shift: boolean;
    operator_id: string | null;
    operator_name: string | null;
    shift_id: string | null;
    gps_lat: number | null;
    gps_lon: number | null;
    fuel_level_pct: number | null;
    ground_speed_kmh: number | null;
    is_idle: boolean | null;
  };
  health_overall: number | null;
  open_alerts: number;
  worst_severity: SeverityLevel | null;
}

export interface Fleet {
  now: string;
  machines: FleetMachine[];
  geofences: Geofence[];
}

/** Latest `maintenance_predictions` row per machine (jsonb `top_factors` narrowed, `risk_band` added). */
export type MaintenancePrediction = Omit<Tables<'maintenance_predictions'>, 'top_factors'> & {
  top_factors: Array<{ feature: string; shap: number; label?: string }>;
  risk_band?: 'low' | 'medium' | 'high';
};

export type FleetMetricRow = Tables<'fleet_metrics_weekly'>;

export interface PcaPoint {
  entity_id: string;
  week_start: string;
  pc1: number;
  pc2: number;
  cluster_id: number;
  cluster_label: string;
}
export interface PcaSet {
  explained_variance: [number, number];
  loadings: Record<string, { pc1: number; pc2: number }>;
  points: PcaPoint[];
}
export interface Clusters {
  week_start: string;
  metrics: FleetMetricRow[];
  pca: { operator: PcaSet; machine: PcaSet };
  names: Record<string, string>;
}

export interface ScenarioStep {
  id?: string;
  title?: string;
  prompt: string;
  image_prompt?: string;
  choices: string[];
  answer: number;
  explanation?: string;
  module_id?: string;
}
/** A `training_modules` row (jsonb `scenario` narrowed). */
export type TrainingModule = Omit<Tables<'training_modules'>, 'scenario' | 'machine_types'> & {
  machine_types: MachineType[];
  scenario: { steps: ScenarioStep[] } | null;
};
export type TrainingRecord = Tables<'training_records'>;
export type TrainingRecommendation = Tables<'training_recommendations'>;
export interface Training {
  modules: TrainingModule[];
  records: TrainingRecord[];
  recommendations: TrainingRecommendation[];
}

export interface ChatSource {
  document_id: number;
  title: string;
  chunk_index: number;
}
/** POST /chat response. The backend also sends `cached` and `pre_generated`; the UI ignores them. */
export interface ChatAnswer {
  answer: string;
  sources: ChatSource[];
  cached?: boolean;
  pre_generated?: boolean;
}
export interface ChatExamples {
  session_id: string;
  examples: Array<ChatAnswer & { question: string }>;
}

/** POST /plan/re-evaluate response (with the optional extra fields). */
export interface PlanResult {
  shift_id: string;
  fits: string[];
  moved_to_next_shift: string[];
  new_order: string[];
  explanation: string;
  triggers?: string[];
  available_min?: number;
  schedule?: Array<{ task_id: string; priority: number; p50_min: number; fits: boolean; start: string | null; end: string | null }>;
  break?: { start: string; end: string; minutes: number } | null;
  /** When the plan was re-evaluated (mock files carry it). */
  now?: string;
}

export interface MachineSignals {
  machine_id: string;
  to: string;
  minutes: number;
  series: Partial<Record<keyof Telemetry, Array<number | null>>>;
}

/** GET /replay/status */
export interface ReplayStatus {
  running: boolean;
  machine_ids: string[];
  speed: number | null;
  replay_ts: string | null;
  source: 'supabase' | 'parquet' | null;
  scenarios: Record<string, string>;
}
