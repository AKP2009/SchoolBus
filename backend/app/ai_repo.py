"""Database access for the AI features: RAG chat, handover summaries, training recommendations.

Kept apart from `app.repo.Repo` (telemetry, tasks, alerts) so each can be faked on its own.
`SupabaseAiRepo` uses the service-role client (bypasses RLS: routers check permissions first);
tests use `tests/fakes.py::MemoryAiRepo`. All methods are sync: call them in the threadpool.
"""

from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from typing import Any, Protocol

from app.repo import iso

OPEN_RECOMMENDATION = ("pending", "accepted")


class AiRepo(Protocol):
    # RAG
    def match_chunks(
        self, embedding: list[float], match_count: int, min_similarity: float
    ) -> list[dict[str, Any]]: ...
    def insert_chat_messages(self, rows: list[dict[str, Any]]) -> None: ...

    # handover
    def shift_full(self, shift_id: str) -> dict[str, Any] | None: ...
    def open_alerts_at(self, machine_id: str, at: datetime) -> list[dict[str, Any]]: ...
    def machine_safety_events(
        self, machine_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]: ...
    def latest_maintenance(self, machine_id: str, at: datetime) -> dict[str, Any] | None: ...
    def shift_tasks(self, shift_id: str) -> list[dict[str, Any]]: ...
    def save_handover(self, shift_id: str, summary: str, generated_at: datetime) -> None: ...
    def shifts_needing_handover(
        self, end_from: datetime, end_to: datetime, machine_ids: list[str] | None
    ) -> list[dict[str, Any]]: ...

    # training recommendations
    def training_modules(self) -> dict[str, dict[str, Any]]: ...
    def operator_ids(self) -> list[str]: ...
    def fleet_metrics(
        self, operator_id: str, week_from: date, week_to: date
    ) -> list[dict[str, Any]]: ...
    def recommendations(self, operator_id: str) -> list[dict[str, Any]]: ...
    def insert_recommendations(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


class SupabaseAiRepo:
    def __init__(self, client: Any) -> None:
        self.sb = client

    def match_chunks(
        self, embedding: list[float], match_count: int, min_similarity: float
    ) -> list[dict[str, Any]]:
        res = self.sb.rpc(
            "match_document_chunks",
            {
                "query_embedding": embedding,
                "match_count": match_count,
                "min_similarity": min_similarity,
            },
        ).execute()
        return list(res.data or [])

    def insert_chat_messages(self, rows: list[dict[str, Any]]) -> None:
        self.sb.table("chat_messages").insert(rows).execute()

    def shift_full(self, shift_id: str) -> dict[str, Any] | None:
        data = self.sb.table("shifts").select("*").eq("shift_id", shift_id).limit(1).execute().data
        return data[0] if data else None

    def open_alerts_at(self, machine_id: str, at: datetime) -> list[dict[str, Any]]:
        """Alerts raised on the machine by `at` and not resolved by then."""
        return list(
            self.sb.table("alerts")
            .select("ts,alert_code,title,severity,stage,recommended_action,resolved_at")
            .eq("machine_id", machine_id)
            .lte("ts", iso(at))
            .or_(f"resolved_at.is.null,resolved_at.gt.{iso(at)}")
            .order("ts", desc=True)
            .limit(20)
            .execute()
            .data
            or []
        )

    def machine_safety_events(
        self, machine_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        return list(
            self.sb.table("safety_events")
            .select("ts,event_type,severity,distance_m,sector")
            .eq("machine_id", machine_id)
            .gte("ts", iso(start))
            .lt("ts", iso(end))
            .order("ts")
            .limit(200)
            .execute()
            .data
            or []
        )

    def latest_maintenance(self, machine_id: str, at: datetime) -> dict[str, Any] | None:
        data = (
            self.sb.table("maintenance_predictions")
            .select("predicted_at,horizon_hours,failure_probability,likely_component")
            .eq("machine_id", machine_id)
            .lte("predicted_at", iso(at))
            .order("predicted_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return data[0] if data else None

    def shift_tasks(self, shift_id: str) -> list[dict[str, Any]]:
        return list(
            self.sb.table("tasks")
            .select("task_id,sequence_no,task_type,material_type,quantity,unit,status,delay_reason")
            .eq("shift_id", shift_id)
            .order("sequence_no")
            .execute()
            .data
            or []
        )

    def save_handover(self, shift_id: str, summary: str, generated_at: datetime) -> None:
        self.sb.table("shifts").update(
            {"handover_summary": summary, "handover_generated_at": iso(generated_at)}
        ).eq("shift_id", shift_id).execute()

    def shifts_needing_handover(
        self, end_from: datetime, end_to: datetime, machine_ids: list[str] | None
    ) -> list[dict[str, Any]]:
        q = (
            self.sb.table("shifts")
            .select("shift_id,machine_id,end_time")
            .is_("handover_summary", "null")
            .gt("end_time", iso(end_from))
            .lte("end_time", iso(end_to))
        )
        if machine_ids is not None:
            q = q.in_("machine_id", machine_ids)
        return list(q.order("end_time").execute().data or [])

    def training_modules(self) -> dict[str, dict[str, Any]]:
        data = self.sb.table("training_modules").select("module_id,title,topic").execute().data
        return {r["module_id"]: r for r in data or []}

    def operator_ids(self) -> list[str]:
        data = self.sb.table("operators").select("operator_id").order("operator_id").execute()
        return [r["operator_id"] for r in data.data or []]

    def fleet_metrics(
        self, operator_id: str, week_from: date, week_to: date
    ) -> list[dict[str, Any]]:
        return list(
            self.sb.table("fleet_metrics_weekly")
            .select("week_start,cluster_label,idle_pct,time_ratio")
            .eq("entity_type", "operator")
            .eq("entity_id", operator_id)
            .gte("week_start", week_from.isoformat())
            .lte("week_start", week_to.isoformat())
            .order("week_start", desc=True)
            .execute()
            .data
            or []
        )

    def recommendations(self, operator_id: str) -> list[dict[str, Any]]:
        return list(
            self.sb.table("training_recommendations")
            .select("id,module_id,status,created_at,reason")
            .eq("operator_id", operator_id)
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )

    def insert_recommendations(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        return list(self.sb.table("training_recommendations").insert(rows).execute().data or [])


@lru_cache
def get_ai_repo() -> AiRepo:
    from app.db import get_supabase

    return SupabaseAiRepo(get_supabase())
