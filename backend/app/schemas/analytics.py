from datetime import date

from pydantic import BaseModel


class ClusterRunResponse(BaseModel):
    week_start: date
    rows_written: int
    summary: str
    telemetry_source: str | None = None  # parquet | supabase
