from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReplayStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    machine_ids: list[str] = Field(min_length=1)
    from_ts: datetime = Field(alias="from")
    speed: Literal[1, 10, 60] = 1


class ReplayStatus(BaseModel):
    running: bool
    machine_ids: list[str] = Field(default_factory=list)
    speed: int | None = None
    replay_ts: datetime | None = None  # current position in the replayed data
    source: Literal["supabase", "parquet"] | None = None  # where the telemetry is read from
    scenarios: dict[str, str] = Field(default_factory=dict)  # machine_id -> running scenario
