"""Data repository interface with two implementations, selected by DATA_SOURCE.

- CsvRepo reads data/output/*.csv (local dev; writes are logged, not persisted).
- SupabaseRepo reads/writes through the service-role client.

Both expose the frames `ml.inference.task_time` needs, and both seed the ml singleton's
frames so the feature builder sees the same data the API serves.
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd

from app.config import get_settings
from app.errors import ApiError
from ml.inference import task_time as task_time_service
from ml.inference.schemas import Recommendation, TaskTimeContext, TaskTimePrediction

logger = logging.getLogger(__name__)

FRAMES = ("tasks", "shifts", "operators", "machines", "weather", "machine_health_daily")


def _load_csv_frames(data_dir: Path) -> dict[str, "pd.DataFrame"]:
    frames: dict[str, pd.DataFrame] = {}
    for name in FRAMES:
        df = pd.read_csv(data_dir / f"{name}.csv")
        if name == "weather":
            df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
        if name == "shifts":
            df["start_time"] = pd.to_datetime(df["start_time"], utc=True, format="ISO8601")
        if name == "tasks":
            df["scheduled_start"] = pd.to_datetime(
                df["scheduled_start"], utc=True, format="ISO8601"
            )
        frames[name] = df
    return frames


@runtime_checkable
class Repo(Protocol):
    def frames(self) -> dict[str, "pd.DataFrame"]: ...
    def context(self) -> TaskTimeContext: ...
    def open_recommendations(self, operator_id: str) -> list[Recommendation]: ...
    def save_predictions(self, predictions: list[TaskTimePrediction]) -> None: ...
    def save_recommendations(self, recs: list[Recommendation]) -> None: ...


class CsvRepo:
    """Local development over the generator's CSVs. Writes are logged only."""

    def __init__(self, data_dir: Path):
        self._dir = data_dir
        self._frames: dict[str, pd.DataFrame] | None = None

    def frames(self) -> dict[str, "pd.DataFrame"]:
        if self._frames is None:
            self._frames = _load_csv_frames(self._dir)
            task_time_service.reload_state(str(self._dir), **self._frames)
        return self._frames

    def context(self) -> TaskTimeContext:
        f = self.frames()
        return TaskTimeContext(
            shifts=f["shifts"],
            operators=f["operators"],
            machines=f["machines"],
            weather=f["weather"],
            machine_health_daily=f["machine_health_daily"],
        )

    def open_recommendations(self, operator_id: str) -> list[Recommendation]:
        return []  # no recommendations store in CSV mode

    def save_predictions(self, predictions: list[TaskTimePrediction]) -> None:
        logger.info("csv repo: %d predictions (not persisted)", len(predictions))

    def save_recommendations(self, recs: list[Recommendation]) -> None:
        logger.info("csv repo: %d recommendations (not persisted)", len(recs))


class SupabaseRepo:
    """Service-role access to Supabase. The service-role key lives only in backend/.env."""

    def __init__(self, url: str, service_role_key: str):
        from supabase import create_client

        self._client = create_client(url, service_role_key)
        self._frames: dict[str, pd.DataFrame] | None = None

    def _select(self, table: str) -> "pd.DataFrame":
        data = self._client.table(table).select("*").execute().data
        return pd.DataFrame(data if data else [])

    def frames(self) -> dict[str, "pd.DataFrame"]:
        if self._frames is None:
            self._frames = {name: self._select(name) for name in FRAMES}
            task_time_service.reload_state(**self._frames)
        return self._frames

    def context(self) -> TaskTimeContext:
        f = self.frames()
        return TaskTimeContext(
            shifts=f["shifts"],
            operators=f["operators"],
            machines=f["machines"],
            weather=f["weather"],
            machine_health_daily=f["machine_health_daily"],
        )

    def open_recommendations(self, operator_id: str) -> list[Recommendation]:
        data = (
            self._client.table("training_recommendations")
            .select("operator_id, module_id, reason, trigger_metric, trigger_value, status")
            .eq("operator_id", operator_id)
            .in_("status", ["pending", "accepted"])
            .execute()
            .data
            or []
        )
        return [Recommendation(**r, task_type="") for r in data]

    def save_predictions(self, predictions: list[TaskTimePrediction]) -> None:
        for p in predictions:
            self._client.table("tasks").update(
                {
                    "predicted_p10_min": round(p.p10_min, 1),
                    "predicted_p50_min": round(p.p50_min, 1),
                    "predicted_p90_min": round(p.p90_min, 1),
                    "prediction_factors": [f.model_dump() for f in p.factors],
                    "standard_min": round(p.standard_min, 1),
                    "expected_efficiency": round(p.expected_efficiency, 3),
                }
            ).eq("task_id", p.task_id).execute()
        logger.info("supabase: wrote %d task predictions", len(predictions))

    def save_recommendations(self, recs: list[Recommendation]) -> None:
        for r in recs:
            self._client.table("training_recommendations").insert(
                {
                    "operator_id": r.operator_id,
                    "module_id": r.module_id,
                    "reason": r.reason,
                    "trigger_metric": r.trigger_metric,
                    "trigger_value": r.trigger_value,
                }
            ).execute()
        logger.info("supabase: inserted %d recommendations", len(recs))


@lru_cache
def get_repo() -> Repo:
    """Singleton repo selected by DATA_SOURCE; warms the ml inference singleton."""
    settings = get_settings()
    repo: Repo
    if settings.data_source == "supabase":
        if not (settings.supabase_url and settings.supabase_service_role_key):
            raise ApiError(500, "CONFIG", "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
        repo = SupabaseRepo(settings.supabase_url, settings.supabase_service_role_key)
    else:
        if not settings.data_dir.is_dir():
            raise ApiError(500, "DATA_NOT_FOUND", f"data dir {settings.data_dir} does not exist")
        repo = CsvRepo(settings.data_dir)
    repo.frames()  # warm: one parse shared by every request
    return repo
