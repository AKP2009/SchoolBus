"""`POST /events`: vision / voice events -> safety_events, alerts, fatigue_log + WebSocket.

Each alert event is pushed as a `safety` message on the machine's WebSocket before anything is
written, then stored: an `alerts` row (insert or coalesce, below), the `safety_events` row
linked to it, and an `alert` message with the row id. `fatigue_sample` becomes a `fatigue_log`
row and a `fatigue` message.

**Coalescing.** The vision service re-posts a red proximity zone and closed eyes every 2 s. One
alert per episode: an event of the same machine and kind (type, plus the fatigue reason) within
`COALESCE_S` of the previous one updates the open alert (count, closest distance, severity only
rises) instead of opening a new one. Every event still gets its own `safety_events` row.
An episode with no new event for `EXPIRE_S` is resolved by `expire()` (APScheduler, every 10 s).
SOS is never auto-resolved: the site manager resolves it.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from starlette.concurrency import run_in_threadpool

from app.core.errors import ApiError
from app.repo import Repo, iso
from app.schemas.events import AlertEvent, FatigueSample

log = logging.getLogger(__name__)

COALESCE_S = 30.0  # same kind within this many seconds -> same alert
EXPIRE_S = 30.0  # no event for this long -> the alert is resolved
STALE_MIN = 10.0  # GET /operator/{id}/fatigue: older rows are flagged stale
LIVE_FATIGUE_MIN = 30.0  # plan: a fatigue_log row this recent (wall clock) is "live"
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2, "emergency": 3}
SHIFT_RE = re.compile(r"^SH-\d{4}-\d{2}-\d{2}-(M\d+)-[DN]$")

WHERE = {
    "front": "in front of the machine",
    "rear": "behind the machine",
    "left": "on the left",
    "right": "on the right",
    "cab": "in the cab",
}


class Pusher(Protocol):
    async def send(self, machine_id: str, kind: str, data: Any) -> None: ...


@dataclass
class Episode:
    """One open vision alert that later events of the same kind update."""

    alert_id: int
    alert_code: str
    severity: str
    machine_id: str | None
    title: str
    recommended_action: str | None
    evidence: dict[str, Any]
    auto_resolve: bool
    last_seen: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------------------------
# Alert text (models.md §5, §6; design.md: plain words, no raw codes in titles)
# ---------------------------------------------------------------------------------------------
def alert_spec(ev: AlertEvent, operator_name: str | None = None) -> dict[str, Any]:
    """alert_code, category, source, stage, title, message, recommended_action for one event."""
    sev = ev.severity.value if ev.severity else "warning"
    red = sev == "critical"
    d = ev.details or {}
    thing = str(d.get("class") or "person").capitalize()
    where = WHERE.get(ev.sector.value if ev.sector else "", "near the machine")
    moving = " and approaching" if ev.approaching else ""
    if ev.type in ("proximity_breach", "blindspot_intrusion"):
        blind = ev.type == "blindspot_intrusion"
        code = ("BLINDSPOT" if blind else "PROXIMITY") + ("_RED" if red else "_ORANGE")
        dist = f"{ev.distance_m:.1f} m" if ev.distance_m is not None else "close"
        title = f"{thing} {dist} {where}" + (" — blind spot" if blind else "")
        sector = ev.sector.value if ev.sector else "area"
        action = (
            f"Stop. Don't move until the {sector} is clear."
            if red
            else f"Slow down and check the {sector} before moving."
        )
        message = f"{thing} detected {dist} {where}{moving} (camera, {sector} sector)."
        return {
            "alert_code": code,
            "category": "safety",
            "source": "vision",
            "stage": "warn",
            "title": title,
            "message": message,
            "recommended_action": action,
        }
    if ev.type == "fatigue_high":
        if d.get("reason") == "eyes_closed":
            secs = d.get("eyes_closed_s")
            took = f" for {float(secs):.1f} s" if isinstance(secs, int | float) else ""
            return {
                "alert_code": "EYES_CLOSED",
                "category": "safety",
                "source": "vision",
                "stage": "warn",
                "title": f"Eyes closed{took} while moving",
                "message": f"The cab camera saw the operator's eyes closed{took} while the "
                "machine was moving.",
                "recommended_action": "Stop the machine safely and take a break.",
            }
        return {
            "alert_code": "FATIGUE_HIGH",
            "category": "safety",
            "source": "vision",
            "stage": "warn",
            "title": "Operator fatigue is high",
            "message": "Fatigue level is high (eye closure, yawning, head nodding, time on "
            "shift).",
            "recommended_action": "Take a 15-minute break at the next safe point.",
        }
    if ev.type == "phone_use":
        return {
            "alert_code": "PHONE_USE",
            "category": "behaviour",
            "source": "vision",
            "stage": "warn",
            "title": "Phone in use in the cab",
            "message": "The cab camera saw a phone in use while operating.",
            "recommended_action": "Put the phone away while operating.",
        }
    who = operator_name or ev.operator_id or "operator"
    on = f" on {ev.machine_id}" if ev.machine_id else ""
    return {
        "alert_code": "SOS",
        "category": "emergency",
        "source": "operator",
        "stage": "escalated",
        "title": f"SOS from {who}{on}",
        "message": str(d.get("message") or f"{who} pressed SOS{on}. Site manager notified."),
        "recommended_action": "Site manager: call the operator and send help.",
    }


def _key(ev: AlertEvent, machine_id: str | None) -> str:
    kind = ev.type
    if ev.type == "fatigue_high" and (ev.details or {}).get("reason") == "eyes_closed":
        kind += ":eyes_closed"
    return f"{machine_id or ev.operator_id}|{kind}"


def _now() -> datetime:
    return datetime.now(UTC)


class EventService:
    def __init__(self, repo: Repo, pusher: Pusher) -> None:
        self.repo = repo
        self.pusher = pusher
        self.episodes: dict[str, Episode] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._machines: dict[str, dict[str, Any]] = {}
        self._operators: dict[str, dict[str, Any]] = {}

    # -- lookups (static master data, cached once found) ---------------------------------------
    async def machine(self, machine_id: str) -> dict[str, Any]:
        m = self._machines.get(machine_id)
        if m is None:
            m = await run_in_threadpool(self.repo.machine, machine_id)
            if m is None:
                raise ApiError(404, "UNKNOWN_MACHINE", f"Unknown machine {machine_id}.")
            self._machines[machine_id] = m
        return m

    async def operator(self, operator_id: str) -> dict[str, Any]:
        o = self._operators.get(operator_id)
        if o is None:
            o = await run_in_threadpool(self.repo.operator, operator_id)
            if o is None:
                raise ApiError(404, "UNKNOWN_OPERATOR", f"Unknown operator {operator_id}.")
            self._operators[operator_id] = o
        return o

    # -- alert events --------------------------------------------------------------------------
    async def handle_alert(self, ev: AlertEvent) -> tuple[int, int]:
        """Returns (alert_id, safety_event_id)."""
        machine = await self.machine(ev.machine_id) if ev.machine_id else None
        operator = await self.operator(ev.operator_id) if ev.operator_id else None
        site_id = (machine or {}).get("site_id") or (operator or {}).get("site_id")
        mid = ev.machine_id

        if mid:  # tell the cab first; the database can take a moment
            await self.pusher.send(
                mid,
                "safety",
                {
                    "type": ev.type,
                    "severity": ev.severity.value if ev.severity else None,
                    "distance_m": ev.distance_m,
                    "sector": ev.sector.value if ev.sector else None,
                    "approaching": ev.approaching,
                },
            )
            lvl, score = ev.details.get("fatigue_level"), ev.details.get("fatigue_score")
            if ev.type == "fatigue_high" and lvl in ("low", "medium", "high"):
                if isinstance(score, int | float):
                    await self.pusher.send(
                        mid, "fatigue", {"fatigue_level": lvl, "fatigue_score": float(score)}
                    )

        key = _key(ev, mid)
        async with self._locks[key]:
            alert_id, pushed = await self._alert_for(ev, key, site_id)
            row = {
                "ts": iso(ev.ts),
                "site_id": site_id,
                "machine_id": mid,
                "operator_id": ev.operator_id,
                "event_type": ev.type,
                "severity": ev.severity.value if ev.severity else "warning",
                "distance_m": ev.distance_m,
                "sector": ev.sector.value if ev.sector else None,
                "approaching": ev.approaching,
                "details": ev.details or None,
                "alert_id": alert_id,
            }
            event_id = await run_in_threadpool(self.repo.insert_safety_event, row)
        if pushed is not None and mid:
            await self.pusher.send(mid, "alert", pushed)
        return alert_id, event_id

    async def _alert_for(
        self, ev: AlertEvent, key: str, site_id: str | None
    ) -> tuple[int, dict[str, Any] | None]:
        """Insert a new alert or update the open episode. Returns (alert_id, WebSocket `alert`
        data if the alert opened or its severity rose, else None)."""
        spec = alert_spec(ev)
        sev = ev.severity.value if ev.severity else "warning"
        ep = self.episodes.get(key)
        now = time.monotonic()
        if ep is not None and now - ep.last_seen <= COALESCE_S:
            ep.last_seen = now
            e = ep.evidence
            e["count"] = int(e.get("count", 1)) + 1
            e["last_ts"] = iso(ev.ts)
            if ev.distance_m is not None:
                e["last_distance_m"] = ev.distance_m
                prev = e.get("min_distance_m")
                e["min_distance_m"] = ev.distance_m if prev is None else min(prev, ev.distance_m)
            fields: dict[str, Any] = {"evidence": e}
            rose = SEVERITY_RANK[sev] > SEVERITY_RANK[ep.severity]
            if rose:
                ep.severity, ep.alert_code = sev, spec["alert_code"]
                ep.title, ep.recommended_action = spec["title"], spec["recommended_action"]
                e.setdefault("stages", []).append({"stage": "warn", "ts": iso(ev.ts), "why": sev})
                fields.update(
                    severity=sev,
                    alert_code=spec["alert_code"],
                    title=spec["title"],
                    message=spec["message"],
                    recommended_action=spec["recommended_action"],
                )
            await run_in_threadpool(self.repo.update_alert, ep.alert_id, fields)
            return ep.alert_id, self._stream(ep, spec["stage"]) if rose else None
        if ep is not None and ep.auto_resolve:
            # quiet for longer than COALESCE_S but not expired yet: close it before the new
            # alert takes its place, or it would stay open forever
            await self._resolve(ep)

        evidence: dict[str, Any] = {
            "event_type": ev.type,
            "sector": ev.sector.value if ev.sector else None,
            "distance_m": ev.distance_m,
            "min_distance_m": ev.distance_m,
            "approaching": ev.approaching,
            "details": ev.details,
            "count": 1,
            "first_ts": iso(ev.ts),
            "last_ts": iso(ev.ts),
            "stages": [{"stage": spec["stage"], "ts": iso(ev.ts)}],
        }
        row = {
            "ts": iso(ev.ts),
            "site_id": site_id,
            "machine_id": ev.machine_id,
            "operator_id": ev.operator_id,
            "source": spec["source"],
            "category": spec["category"],
            "alert_code": spec["alert_code"],
            "title": spec["title"],
            "message": spec["message"],
            "recommended_action": spec["recommended_action"],
            "severity": sev,
            "stage": spec["stage"],
            "evidence": evidence,
        }
        alert_id = await run_in_threadpool(self.repo.insert_alert, row)
        ep = Episode(
            alert_id=alert_id,
            alert_code=spec["alert_code"],
            severity=sev,
            machine_id=ev.machine_id,
            title=spec["title"],
            recommended_action=spec["recommended_action"],
            evidence=evidence,
            auto_resolve=ev.type != "sos",
            last_seen=now,
        )
        self.episodes[key] = ep
        return alert_id, self._stream(ep, spec["stage"])

    @staticmethod
    def _stream(ep: Episode, stage: str) -> dict[str, Any]:
        return {
            "id": ep.alert_id,
            "alert_code": ep.alert_code,
            "severity": ep.severity,
            "stage": stage,
            "title": ep.title,
            "recommended_action": ep.recommended_action,
        }

    async def _resolve(self, ep: Episode) -> bool:
        """Mark the episode's alert resolved and tell the cab. Caller holds the key's lock."""
        at = _now()
        e = ep.evidence
        e.setdefault("stages", []).append({"stage": "resolved", "ts": iso(at)})
        try:
            first = datetime.fromisoformat(str(e["first_ts"]).replace("Z", "+00:00"))
            last = datetime.fromisoformat(str(e["last_ts"]).replace("Z", "+00:00"))
            e["duration_s"] = round((last - first).total_seconds(), 1)
        except (KeyError, ValueError):
            pass
        try:
            await run_in_threadpool(
                self.repo.update_alert,
                ep.alert_id,
                {"stage": "resolved", "resolved_at": iso(at), "evidence": e},
            )
        except Exception as err:  # noqa: BLE001 - keep expiring the others
            log.error("resolving alert %s failed: %s", ep.alert_id, err)
            return False
        if ep.machine_id:
            await self.pusher.send(ep.machine_id, "alert", self._stream(ep, "resolved"))
        return True

    async def expire(self, older_than_s: float = EXPIRE_S) -> int:
        """Resolve vision alerts with no event for `older_than_s`; forget old SOS episodes.
        Then sweep the database for open vision alerts this process doesn't track (opened before
        a restart, e.g. `uvicorn --reload`): nothing would ever resolve them otherwise.
        Runs on its own timer (APScheduler, every 10 s), with or without a replay.
        Returns the number resolved."""
        now = time.monotonic()
        resolved = 0
        for key, ep in list(self.episodes.items()):
            if now - ep.last_seen <= older_than_s:
                continue
            async with self._locks[key]:
                if (
                    self.episodes.get(key) is not ep
                    or time.monotonic() - ep.last_seen <= older_than_s
                ):
                    continue
                del self.episodes[key]
                if ep.auto_resolve and await self._resolve(ep):
                    resolved += 1
        return resolved + await self._sweep_orphans(older_than_s)

    async def _sweep_orphans(self, older_than_s: float) -> int:
        try:
            rows = await run_in_threadpool(self.repo.open_vision_alerts)
        except Exception as err:  # noqa: BLE001 - try again on the next tick
            log.error("listing open vision alerts failed: %s", err)
            return 0
        tracked = {ep.alert_id for ep in self.episodes.values()}
        cutoff = _now() - timedelta(seconds=older_than_s)
        resolved = 0
        for r in rows:
            if r["id"] in tracked:
                continue
            e = dict(r.get("evidence") or {})
            last = str(e.get("last_ts") or r["ts"]).replace("Z", "+00:00")
            if datetime.fromisoformat(last) > cutoff:
                continue  # just inserted: its episode is about to be registered
            ep = Episode(
                alert_id=int(r["id"]),
                alert_code=r["alert_code"],
                severity=r["severity"],
                machine_id=r.get("machine_id"),
                title=r["title"],
                recommended_action=r.get("recommended_action"),
                evidence=e,
                auto_resolve=True,
            )
            if await self._resolve(ep):
                log.info("resolved orphaned vision alert %s (%s)", ep.alert_id, ep.alert_code)
                resolved += 1
        return resolved

    # -- fatigue samples -----------------------------------------------------------------------
    async def handle_fatigue(self, ev: FatigueSample) -> int:
        await self.operator(ev.operator_id)
        shift = await run_in_threadpool(self.repo.shift, ev.shift_id)
        if shift is None:
            # vision builds a shift id for "today" that the loaded data may not have
            log.warning("fatigue_sample: unknown shift %s, stored without it", ev.shift_id)
        mid = ev.machine_id or (shift or {}).get("machine_id")
        if mid is None:
            m = SHIFT_RE.match(ev.shift_id)
            mid = m.group(1) if m else None
        if mid:
            await self.pusher.send(
                mid,
                "fatigue",
                {"fatigue_level": ev.fatigue_level.value, "fatigue_score": ev.fatigue_score},
            )
        row = {
            "ts": iso(ev.ts),
            "operator_id": ev.operator_id,
            "shift_id": ev.shift_id if shift is not None else None,
            "ear_avg": ev.ear_avg,
            "perclos_60s": ev.perclos_60s,
            "yawn_count": ev.yawn_count,
            "head_down_events": ev.head_down_events,
            "phone_detected": ev.phone_detected,
            "fatigue_score": ev.fatigue_score,
            "fatigue_level": ev.fatigue_level.value,
        }
        return await run_in_threadpool(self.repo.insert_fatigue, row)


# ---------------------------------------------------------------------------------------------
# Fatigue reads
# ---------------------------------------------------------------------------------------------
def latest_fatigue(repo: Repo, operator_id: str) -> dict[str, Any] | None:
    """Latest fatigue_log row with its wall-clock age (GET /operator/{id}/fatigue)."""
    row = repo.latest_fatigue(operator_id)
    if row is None:
        return None
    ts = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
    age = max((_now() - ts).total_seconds() / 60, 0.0)
    return {**row, "ts": ts, "age_min": round(age, 1), "stale": age > STALE_MIN}


def fatigue_for_plan(repo: Repo, operator_id: str, plan_now: datetime) -> dict[str, Any] | None:
    """The fatigue_log row the plan's fatigue trigger reads: a live row from the cab camera
    (written in the last LIVE_FATIGUE_MIN minutes of wall-clock time) wins; otherwise the latest
    row at or before the plan's clock (replay time when the machine is being replayed)."""
    live = repo.latest_fatigue(operator_id, since=_now() - timedelta(minutes=LIVE_FATIGUE_MIN))
    return live or repo.latest_fatigue(operator_id, until=plan_now)
