"""CSV output. Column lists mirror supabase/migrations/001_init.sql exactly
(identity `id` and default-only columns such as created_at are omitted).

CSV conventions (Postgres COPY ... CSV compatible): empty field = NULL, text[] as `{a,b}`,
timestamptz as ISO 8601 in UTC with offset, dates as YYYY-MM-DD.
"""

from pathlib import Path

import pandas as pd

TABLE_COLUMNS: dict[str, list[str]] = {
    "sites": ["site_id", "name", "site_type", "lat", "lon", "timezone"],
    "operators": [
        "operator_id",
        "site_id",
        "full_name",
        "experience_years",
        "certification_level",
        "languages",
        "preferred_shift",
        "skill_score",
        "personality",
    ],
    "machines": [
        "machine_id",
        "site_id",
        "machine_type",
        "model",
        "serial_no",
        "year",
        "total_engine_hours",
        "hours_since_service",
        "service_interval_hours",
        "status",
        "health_score",
    ],
    "shifts": [
        "shift_id",
        "site_id",
        "operator_id",
        "machine_id",
        "shift_type",
        "shift_date",
        "start_time",
        "end_time",
        "fuel_start_pct",
        "fuel_end_pct",
        "handover_notes",
        "issues_reported",
        "handover_summary",
        "handover_generated_at",
    ],
    "tasks": [
        "task_id",
        "site_id",
        "shift_id",
        "machine_id",
        "operator_id",
        "sequence_no",
        "task_date",
        "task_type",
        "material_type",
        "quantity",
        "unit",
        "terrain_slope_deg",
        "haul_distance_m",
        "priority",
        "scheduled_start",
        "predicted_p10_min",
        "predicted_p50_min",
        "predicted_p90_min",
        "prediction_factors",
        "actual_start",
        "actual_end",
        "actual_duration_min",
        "status",
        "delay_reason",
    ],
    "weather": [
        "site_id",
        "ts",
        "temp_c",
        "humidity_pct",
        "rain_mm",
        "wind_kmh",
        "visibility_m",
        "dust_index",
    ],
    "maintenance_log": [
        "machine_id",
        "event_date",
        "engine_hours_at_event",
        "component",
        "event_type",
        "downtime_hours",
        "cost_inr",
        "notes",
    ],
    # generator-only helper, not a DB table
    "machine_health_daily": ["machine_id", "date", "health_score"],
}

# `not null` columns in 001_init.sql (defaults included: we always write a value)
NOT_NULL: dict[str, list[str]] = {
    "sites": ["site_id", "name", "site_type", "lat", "lon", "timezone"],
    "operators": [
        "operator_id",
        "site_id",
        "full_name",
        "experience_years",
        "certification_level",
        "languages",
        "preferred_shift",
        "skill_score",
    ],
    "machines": [
        "machine_id",
        "site_id",
        "machine_type",
        "model",
        "total_engine_hours",
        "hours_since_service",
        "service_interval_hours",
        "status",
        "health_score",
    ],
    "shifts": [
        "shift_id",
        "site_id",
        "operator_id",
        "machine_id",
        "shift_type",
        "shift_date",
        "start_time",
        "end_time",
    ],
    "tasks": [
        "task_id",
        "site_id",
        "shift_id",
        "machine_id",
        "operator_id",
        "sequence_no",
        "task_date",
        "task_type",
        "material_type",
        "quantity",
        "unit",
        "terrain_slope_deg",
        "priority",
        "status",
    ],
    "weather": ["site_id", "ts"],
    "maintenance_log": [
        "machine_id",
        "event_date",
        "engine_hours_at_event",
        "component",
        "event_type",
    ],
    "machine_health_daily": ["machine_id", "date", "health_score"],
}


def _pg_array(v: object) -> object:
    if isinstance(v, (list, tuple)):
        return "{" + ",".join(str(x) for x in v) + "}"
    return v


def to_table(df: pd.DataFrame, table: str) -> pd.DataFrame:
    """Select and order the DB columns (drops generator helper columns)."""
    missing = set(TABLE_COLUMNS[table]) - set(df.columns)
    assert not missing, f"{table}: missing columns {missing}"
    return df[TABLE_COLUMNS[table]].copy()


def write_csv(df: pd.DataFrame, table: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = to_table(df, table)
    for col in out.columns:
        if out[col].dtype == object:
            out[col] = out[col].map(_pg_array)
    path = out_dir / f"{table}.csv"
    out.to_csv(path, index=False)
    return path
