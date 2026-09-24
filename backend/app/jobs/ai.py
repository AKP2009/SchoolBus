"""AI jobs (docs/supabase.md §10): handover summary at shift end, daily training recommendations.

Handover: every `HANDOVER_TICK_S` of wall-clock time, shifts that ended in the last
`LOOKBACK` and have no `handover_summary` get one. "Ended" is measured on the replay clock for
machines in the running replay (the synthetic shifts are in the past), and on the wall clock
for everything else. At most one summary per tick (free-tier LLM rate limits); a failed shift
is retried after `RETRY_AFTER`. Without LLM_API_KEY the job logs once and does nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.ai_repo import AiRepo
from app.llm import config_error, get_llm
from app.repo import Repo
from app.services import handover
from app.services.recommender import default_as_of, recommend
from app.services.telemetry import TelemetryHistory

log = logging.getLogger(__name__)

LOOKBACK = timedelta(hours=12)
RETRY_AFTER = timedelta(minutes=10)


class HandoverJob:
    def __init__(self, ai: AiRepo, engine: Any) -> None:
        self.ai = ai
        self.engine = engine
        self.failed: dict[str, datetime] = {}  # shift_id -> wall-clock time of the failure
        self._warned = False

    def candidates(self, now: datetime) -> list[dict[str, Any]]:
        eng = self.engine
        replayed: list[str] = []
        out: list[dict[str, Any]] = []
        if eng.running and eng.replay_ts is not None:
            replayed = list(eng.streams)
            out += self.ai.shifts_needing_handover(
                eng.replay_ts - LOOKBACK, eng.replay_ts, replayed
            )
        live = self.ai.shifts_needing_handover(now - LOOKBACK, now, None)
        out += [s for s in live if s["machine_id"] not in replayed]
        return [
            s
            for s in out
            if s["shift_id"] not in self.failed or now - self.failed[s["shift_id"]] >= RETRY_AFTER
        ]

    async def tick(self, now: datetime | None = None) -> str | None:
        """Restore pre-generated summaries a reset cleared (no LLM), then summarise at most one
        ended shift. Returns its shift_id, or None."""
        try:
            restored = await run_in_threadpool(handover.restore_pregenerated, self.ai)
        except Exception:  # noqa: BLE001 - the live path below still runs
            log.exception("restoring pre-generated handovers failed")
            restored = []
        if restored:
            log.info("restored pre-generated handover for %s", ", ".join(restored))
        reason = config_error()
        if reason is not None:
            if not self._warned:
                log.warning("handover job disabled: %s", reason)
                self._warned = True
            return None
        now = now or datetime.now(UTC)
        todo = await run_in_threadpool(self.candidates, now)
        if not todo:
            return None
        sid = todo[0]["shift_id"]
        try:
            summary = await run_in_threadpool(handover.generate, self.ai, get_llm(), sid)
        except Exception:  # noqa: BLE001 - retried later
            self.failed[sid] = now
            log.exception("handover summary for %s failed", sid)
            return None
        self.failed.pop(sid, None)
        log.info("handover summary for %s:\n%s", sid, summary)
        return sid


def recommend_all(
    repo: Repo, ai: AiRepo, telemetry: TelemetryHistory, as_of: datetime | None = None
) -> int:
    """Daily job body: the recommender for every operator. Returns rows written."""
    as_of = as_of or default_as_of(repo)
    if as_of is None:
        log.info("training recommendations: no shifts yet")
        return 0
    written = 0
    for op in ai.operator_ids():
        try:
            written += len(recommend(repo, ai, telemetry, op, as_of)["created"])
        except Exception:  # noqa: BLE001 - keep the other operators going
            log.exception("training recommendations for %s failed", op)
    log.info("training recommendations as of %s: %d new", as_of, written)
    return written
