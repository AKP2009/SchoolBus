// Hand-written until the generated types exist (web/src/types/supabase.ts, see CLAUDE.md).
// Enum values and column names follow supabase/migrations/001_init.sql; API shapes follow
// docs/api_contract.md. Mocks in web/src/mocks/ are built to these shapes by scripts/build_mocks.py.

export type SeverityLevel = 'info' | 'warning' | 'critical' | 'emergency';
export type AlertStage = 'warn' | 'derate' | 'recommend_shutdown' | 'escalated' | 'resolved';
export type TaskType = 'dig' | 'trench' | 'load' | 'haul' | 'grade' | 'backfill';
export type MaterialType = 'clay' | 'sand' | 'gravel' | 'rock' | 'topsoil';
export type TaskStatus = 'scheduled' | 'in_progress' | 'completed' | 'delayed' | 'cancelled';
export type CameraSector = 'front' | 'rear' | 'left' | 'right' | 'cab';
export type FatigueLevel = 'low' | 'medium' | 'high';
export type MachineType = 'excavator' | 'wheel_loader' | 'dozer' | 'articulated_truck';
export type MachineStatus = 'active' | 'idle' | 'maintenance' | 'down';
export type ShiftType = 'day' | 'night';
export type AlertSource = 'rule' | 'anomaly_model' | 'predictive_model' | 'vision' | 'operator' | 'system';
export type AlertCategory = 'internal' | 'safety' | 'maintenance' | 'behaviour' | 'emergency';
export type SafetyEventType =
  | 'seatbelt_unfastened'
  | 'proximity_breach'
  | 'blindspot_intrusion'
  | 'fatigue_high'
  | 'phone_use'
  | 'tip_risk'
  | 'geofence_breach'
  | 'harsh_maneuver'
  | 'overspeed'
  | 'sos';
export type MachineComponent = 'engine' | 'hydraulics' | 'cooling' | 'electrical' | 'brakes' | 'undercarriage' | 'other';
export type IncidentType = 'near_miss' | 'collision' | 'injury' | 'equipment_damage' | 'spill_leak' | 'other';
export type IncidentStatus = 'open' | 'investigating' | 'closed';
export type ReportChannel = 'form' | 'voice' | 'auto';
export type GeofenceType = 'no_go' | 'pedestrian' | 'power_line' | 'trench' | 'speed_limited';
export type TrainingFormat = 'video' | 'document' | 'quiz' | 'scenario' | 'instructor_session';
export type RecommendationStatus = 'pending' | 'accepted' | 'dismissed' | 'completed';
export type Band = 'green' | 'orange' | 'red';
export type Subsystem = 'engine' | 'cooling' | 'hydraulics' | 'electrical' | 'undercarriage';

/** One entry of `tasks.prediction_factors` (docs/api_contract.md). */
export interface PredictionFactor {
  feature: string;
  label: string;
  impact_min: number;
}

/** The fields TaskCard needs; `TaskRow` is the full `tasks` row. */
export interface Task {
  task_id: string;
  task_type: TaskType;
  material_type: MaterialType;
  quantity: number;
  unit: 'm3' | 'tons';
  predicted_p10_min: number | null;
  predicted_p50_min: number | null;
  predicted_p90_min: number | null;
  prediction_factors: PredictionFactor[] | null;
  status: TaskStatus;
}

export interface TaskRow extends Task {
  site_id: string;
  shift_id: string;
  machine_id: string;
  operator_id: string;
  sequence_no: number;
  task_date: string;
  terrain_slope_deg: number;
  haul_distance_m: number | null;
  priority: 1 | 2 | 3;
  scheduled_start: string | null;
  actual_start: string | null;
  actual_end: string | null;
  actual_duration_min: number | null;
  delay_reason: string | null;
  updated_at: string;
}

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

/** An `alerts` / `v_open_alerts` row. */
export interface AlertRow extends Alert {
  site_id: string;
  operator_id: string | null;
  source: AlertSource;
  category: AlertCategory;
  alert_code: string;
  anomaly_score: number | null;
  evidence: Record<string, unknown> | null;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  resolved_at: string | null;
}

/** Subsystem scores from `machine_health_snapshots`, 0–1, null when unknown. */
export interface HealthScores {
  engine_score: number | null;
  cooling_score: number | null;
  hydraulics_score: number | null;
  electrical_score: number | null;
  undercarriage_score: number | null;
}

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

/** A `telemetry` row as streamed (never anomaly_label / anomaly_type). */
export interface Telemetry {
  ts: string;
  machine_id: string;
  operator_id: string | null;
  shift_id: string | null;
  engine_rpm: number | null;
  engine_load_pct: number | null;
  coolant_temp_c: number | null;
  engine_oil_temp_c: number | null;
  oil_pressure_kpa: number | null;
  hydraulic_pressure_bar: number | null;
  hydraulic_oil_temp_c: number | null;
  fuel_rate_lph: number | null;
  fuel_level_pct: number | null;
  battery_voltage: number | null;
  vibration_rms_g: number | null;
  ground_speed_kmh: number | null;
  pitch_deg: number | null;
  roll_deg: number | null;
  gps_lat: number | null;
  gps_lon: number | null;
  seatbelt_fastened: boolean | null;
  is_idle: boolean | null;
  fault_code: string | null;
}

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
export interface Site {
  site_id: string;
  name: string;
  site_type?: string;
  lat?: number;
  lon?: number;
  timezone?: string;
}

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

export interface ShiftRow {
  shift_id: string;
  operator_id: string;
  machine_id: string;
  shift_type: ShiftType;
  shift_date: string;
  start_time: string;
  end_time: string;
}

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

export interface SafetyEventRow {
  id: number;
  ts: string;
  site_id: string;
  machine_id: string | null;
  operator_id: string | null;
  event_type: SafetyEventType;
  severity: SeverityLevel;
  distance_m: number | null;
  sector: CameraSector | null;
  approaching: boolean | null;
  details: Record<string, unknown> | null;
  alert_id: number | null;
  resolved: boolean;
}

export interface FatigueRow {
  ts: string;
  operator_id: string;
  shift_id: string | null;
  ear_avg: number | null;
  perclos_60s: number | null;
  yawn_count: number;
  head_down_events: number;
  phone_detected: boolean;
  fatigue_score: number;
  fatigue_level: FatigueLevel;
}

export interface IncidentRow {
  id: number;
  client_id: string;
  ts: string;
  site_id: string;
  machine_id: string | null;
  operator_id: string | null;
  incident_type: IncidentType;
  severity: SeverityLevel;
  description: string;
  injury: boolean;
  damage_description: string | null;
  root_cause: string | null;
  reported_via: ReportChannel;
  voice_transcript: string | null;
  ai_summary: string | null;
  linked_alert_id: number | null;
  linked_event_id: number | null;
  media_paths: string[];
  status: IncidentStatus;
  created_by: string | null;
}

export interface GeoPolygon {
  type: 'Polygon';
  /** GeoJSON: [ring][point][lon, lat] */
  coordinates: number[][][];
}

export interface Geofence {
  id: number;
  site_id: string;
  name: string;
  zone_type: GeofenceType;
  polygon: GeoPolygon;
  speed_limit_kmh: number | null;
  active: boolean;
  created_by: string | null;
  created_at: string;
}

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

export interface MaintenancePrediction {
  id: number;
  machine_id: string;
  predicted_at: string;
  horizon_hours: number;
  failure_probability: number;
  likely_component: MachineComponent | null;
  top_factors: Array<{ feature: string; shap: number; label?: string }>;
  model_version: string;
  risk_band?: 'low' | 'medium' | 'high';
}

export interface FleetMetricRow {
  id: number;
  entity_type: 'operator' | 'machine';
  entity_id: string;
  site_id: string;
  week_start: string;
  productive_hours: number | null;
  fuel_per_productive_hour: number | null;
  idle_pct: number | null;
  productivity_per_hour: number | null;
  time_ratio: number | null;
  anomaly_count: number;
  safety_event_count: number;
  efficiency_index: number | null;
  cluster_id: number | null;
  cluster_label: string | null;
  is_outlier: boolean;
  outlier_reason: string | null;
  rank_in_site: number | null;
  verified_by: string | null;
  verified_at: string | null;
}

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
export interface TrainingModule {
  module_id: string;
  title: string;
  topic: string;
  format: TrainingFormat;
  duration_min: number | null;
  difficulty: number | null;
  content_path: string | null;
  target_metric: string | null;
  machine_types: MachineType[];
  languages: string[];
  scenario: { steps: ScenarioStep[] } | null;
}
export interface TrainingRecord {
  id: number;
  client_id: string;
  operator_id: string;
  module_id: string;
  started_at: string;
  completed_at: string | null;
  score: number | null;
  passed: boolean | null;
}
export interface TrainingRecommendation {
  id: number;
  operator_id: string;
  module_id: string;
  reason: string;
  trigger_metric: string | null;
  trigger_value: number | null;
  status: RecommendationStatus;
  created_at: string;
}
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
/** POST /chat response */
export interface ChatAnswer {
  answer: string;
  sources: ChatSource[];
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
