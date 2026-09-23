// Hand-written until the generated types exist (web/src/types/supabase.ts, see CLAUDE.md).
// Enum values and column names follow supabase/migrations/001_init.sql.

export type SeverityLevel = 'info' | 'warning' | 'critical' | 'emergency';
export type AlertStage = 'warn' | 'derate' | 'recommend_shutdown' | 'escalated' | 'resolved';
export type TaskType = 'dig' | 'trench' | 'load' | 'haul' | 'grade' | 'backfill';
export type MaterialType = 'clay' | 'sand' | 'gravel' | 'rock' | 'topsoil';
export type TaskStatus = 'scheduled' | 'in_progress' | 'completed' | 'delayed' | 'cancelled';
export type CameraSector = 'front' | 'rear' | 'left' | 'right' | 'cab';
export type FatigueLevel = 'low' | 'medium' | 'high';

/** One entry of `tasks.prediction_factors` (docs/api_contract.md). */
export interface PredictionFactor {
  feature: string;
  label: string;
  impact_min: number;
}

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

/** Subsystem scores from `machine_health_snapshots`, 0–1, null when unknown. */
export interface HealthScores {
  engine_score: number | null;
  cooling_score: number | null;
  hydraulics_score: number | null;
  electrical_score: number | null;
  undercarriage_score: number | null;
}
