"""Alert rule engine and graded response (docs/models.md §R).

Pure logic, no I/O: `MachineRuleEngine.process(row)` takes one telemetry row of one machine and
returns the alert changes it causes (`AlertEvent`: insert a new alert, or update one on a stage
change, a severity rise or resolution). The replay engine writes them to Supabase and pushes them
on the WebSocket.

How a rule becomes an alert:
1. The rule's evaluator reads the (glitch-cleaned) row and recent history and says, per severity
   level, whether the condition is true right now, plus whether the signal is inside the
   hysteresis band (5% inside the limit).
2. A level is active once its condition has held for its `hold` time. A sample covers its own
   period (60 s for a telemetry minute, 5 s for a scripted seatbelt sample), so "> 100 °C for
   2 min" fires on the 2nd consecutive minute row.
3. The first active level opens the alert at stage `warn`. Severity only rises while it is open.
4. Response per rule: `graded` (critical internal alerts: warn → derate → recommend_shutdown →
   escalated), `escalate` (critical safety alerts: warn → escalated), `none` (stays at warn).
5. The alert resolves once the signal has been inside the hysteresis band for 2 minutes.

Times are data time. The engine never stops a machine: the strongest step is a recommendation to
shut down safely plus escalation to the site manager.
"""

from __future__ import annotations

import math
import uuid
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml

THRESHOLDS_PATH = Path(__file__).with_name("thresholds.yaml")

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2, "emergency": 3}
STAGE_ORDER = ["warn", "derate", "recommend_shutdown", "escalated", "resolved"]
STAGE_RANK = {s: i for i, s in enumerate(STAGE_ORDER)}
CONTIGUOUS_S = 120.0  # rows further apart than this are a data gap: holds restart
HISTORY_MIN = 12  # rolling history kept per machine (HYD_PRESSURE_DROP needs 6 min + 6 min)
DEFAULT_PERIOD_S = 60.0

# Sensor validity ranges, same as model 1 (ml.inference.anomaly.PLAUSIBLE_RANGE). Copied, not
# imported, so the rule engine loads without the ML stack; test_rules checks they match.
PLAUSIBLE_RANGE: dict[str, tuple[float, float]] = {
    "engine_rpm": (0, 3000),
    "engine_load_pct": (-10, 110),
    "coolant_temp_c": (10, 130),
    "oil_pressure_kpa": (20, 800),
    "hydraulic_pressure_bar": (-10, 1000),
    "hydraulic_oil_temp_c": (10, 120),
    "fuel_rate_lph": (0, 80),
    "battery_voltage": (18, 32),
    "vibration_rms_g": (0, 5),
}
ENGINE_RUNNING_RPM = 500

Response = Literal["graded", "escalate", "none"]


def load_thresholds(path: Path | str | None = None) -> dict[str, Any]:
    with open(path or THRESHOLDS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _f(x: Any) -> float | None:
    """A finite float, or None."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _fmt(template: str, **values: Any) -> str:
    """str.format that tolerates missing or None values (leaves the text readable)."""
    safe = {k: (v if v is not None else float("nan")) for k, v in values.items()}
    try:
        return template.format(**safe)
    except (KeyError, ValueError, IndexError):
        return template.split("{", 1)[0].rstrip(" —-") or template


# ---------------------------------------------------------------------------------------------
# Sensor glitch cleaning (model 1's rule, causal per row)
# ---------------------------------------------------------------------------------------------
def implausible(row: Mapping[str, Any]) -> set[str]:
    """Signals whose reading is outside its sensor validity range (or missing)."""
    bad = set()
    for s, (lo, hi) in PLAUSIBLE_RANGE.items():
        v = _f(row.get(s))
        if v is None or v < lo or v > hi:
            bad.add(s)
    if "oil_pressure_kpa" in bad and not ((_f(row.get("engine_rpm")) or 0) > ENGINE_RUNNING_RPM):
        bad.discard("oil_pressure_kpa")
    return bad


def is_glitch(
    row: Mapping[str, Any], prev_raw: Mapping[str, Any] | None, contiguous: bool
) -> str | None:
    """The glitched signal if `row` is a single-minute sensor glitch, else None.

    Same rule as `ml.inference.anomaly.mark_glitches`: exactly one signal is implausible, and it
    was plausible in the previous reading of the same machine (<= 2 min earlier).
    """
    bad = implausible(row)
    if len(bad) != 1 or not contiguous or prev_raw is None:
        return None
    (s,) = bad
    return None if s in implausible(prev_raw) else s


# ---------------------------------------------------------------------------------------------
# Evaluators: row + history -> Reading
# ---------------------------------------------------------------------------------------------
@dataclass
class Reading:
    """What a rule sees in one row."""

    levels: dict[str, bool]  # severity -> condition true in this row (before hold times)
    clear: bool  # inside the hysteresis band: counts towards resolving an open alert
    value: float | None = None  # the watched value, for the title and "value rising"
    worse: float = 1.0  # +1 if a higher value is worse, -1 if lower is worse
    text: dict[str, Any] = field(default_factory=dict)  # extra template values (limit, code...)
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Ctx:
    """Per-machine context the evaluators may use."""

    machine_type: str
    history: deque[dict[str, Any]]  # recent cleaned rows, oldest first, current row last
    thresholds: dict[str, Any]
    geofences: list[dict[str, Any]]
    state: dict[str, dict[str, Any]]  # per-rule scratch space (latches)


def _mean(rows: Iterable[Mapping[str, Any]], key: str) -> float | None:
    vals = [v for v in (_f(r.get(key)) for r in rows) if v is not None]
    return sum(vals) / len(vals) if vals else None


def _window(history: deque[dict[str, Any]], end: datetime, minutes: float) -> list[dict[str, Any]]:
    """Rows with end - minutes < ts <= end (pandas-style trailing time window)."""
    start = end - timedelta(minutes=minutes)
    return [r for r in history if start < r["ts"] <= end]


def eval_threshold(spec: Mapping[str, Any], row: Mapping[str, Any], ctx: Ctx) -> Reading:
    v = _f(row.get(spec["signal"]))
    above = spec.get("direction", "above") == "above"
    pct = ctx.thresholds["hysteresis"]["inside_pct"] / 100
    gate = spec.get("gate")
    gate_ok = True
    if gate:
        g = _f(row.get(gate["signal"]))
        gate_ok = g is not None and g > gate["above"]
    levels = {}
    limits = [lv["limit"] for lv in spec["levels"].values()]
    for name, lv in spec["levels"].items():
        lim = lv["limit"]
        levels[name] = gate_ok and v is not None and (v > lim if above else v < lim)
    # clear band: 5% inside the lowest limit (above) / highest limit (below)
    if v is None:
        clear = False
    elif above:
        clear = v < min(limits) * (1 - pct)
    else:
        clear = v > max(limits) * (1 + pct)
    active = [spec["levels"][n]["limit"] for n, on in levels.items() if on]
    limit = (max(active) if above else min(active)) if active else limits[0]
    return Reading(
        levels=levels,
        clear=clear,
        value=v,
        worse=1.0 if above else -1.0,
        text={"limit": limit},
        evidence={"signal": spec["signal"], "value": v, "limit": limit},
    )


def eval_tilt(spec: Mapping[str, Any], row: Mapping[str, Any], ctx: Ctx) -> Reading:
    p, r = _f(row.get("pitch_deg")), _f(row.get("roll_deg"))
    vals = [abs(x) for x in (p, r) if x is not None]
    v = max(vals) if vals else None
    inside = 1 - ctx.thresholds["hysteresis"]["inside_pct"] / 100
    levels = {n: v is not None and v > lv["limit"] for n, lv in spec["levels"].items()}
    lowest = min(lv["limit"] for lv in spec["levels"].values())
    return Reading(
        levels=levels,
        clear=v is not None and v < lowest * inside,
        value=v,
        evidence={"pitch_deg": p, "roll_deg": r, "tilt_deg": v},
    )


def eval_seatbelt(spec: Mapping[str, Any], row: Mapping[str, Any], ctx: Ctx) -> Reading:
    fastened = row.get("seatbelt_fastened")
    speed = _f(row.get("ground_speed_kmh")) or 0.0
    load = _f(row.get("engine_load_pct")) or 0.0
    moving = speed > ctx.thresholds["moving_kmh"]
    inside = 1 - ctx.thresholds["hysteresis"]["inside_pct"] / 100
    working = moving or load > spec["load_pct"]
    unbuckled = fastened is False
    cond = unbuckled and working
    # inside the band: buckled, or clearly stopped (speed and load 5% inside their limits)
    stopped = speed < ctx.thresholds["moving_kmh"] * inside and load < spec["load_pct"] * inside
    return Reading(
        levels={n: cond for n in spec["levels"]},
        clear=(fastened is True) or stopped,
        value=None,
        evidence={
            "seatbelt_fastened": fastened,
            "ground_speed_kmh": speed,
            "engine_load_pct": load,
        },
    )


def eval_excess_idle(spec: Mapping[str, Any], row: Mapping[str, Any], ctx: Ctx) -> Reading:
    st = ctx.state.setdefault("EXCESS_IDLE", {"start": None, "rpm_sum": 0.0, "n": 0, "last": None})
    ts = row["ts"]
    idle = bool(row.get("is_idle"))
    rpm = _f(row.get("engine_rpm"))
    contiguous = st["last"] is not None and (ts - st["last"]).total_seconds() <= CONTIGUOUS_S
    if idle and rpm is not None:
        if st["start"] is None or not contiguous:
            st.update(start=ts, rpm_sum=0.0, n=0)
        st["rpm_sum"] += rpm
        st["n"] += 1
    else:
        st.update(start=None, rpm_sum=0.0, n=0)
    st["last"] = ts
    period = _f(row.get("_period_s")) or DEFAULT_PERIOD_S
    run_min = ((ts - st["start"]).total_seconds() + period) / 60 if st["start"] else 0.0
    mean_rpm = st["rpm_sum"] / st["n"] if st["n"] else None
    inside = 1 - ctx.thresholds["hysteresis"]["inside_pct"] / 100
    high = mean_rpm is not None and mean_rpm > spec["rpm_above"]
    levels = {n: high and run_min > lv["over_min"] for n, lv in spec["levels"].items()}
    return Reading(
        levels=levels,
        clear=not idle or mean_rpm is None or mean_rpm < spec["rpm_above"] * inside,
        value=run_min,
        text={"minutes": run_min},
        evidence={"idle_min": round(run_min, 1), "mean_rpm": mean_rpm and round(mean_rpm, 1)},
    )


def _point_in_polygon(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            x = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x:
                inside = not inside
    return inside


def speed_limit(row: Mapping[str, Any], ctx: Ctx) -> tuple[float | None, str]:
    """Lowest applicable limit: a speed_limited geofence the machine is in, else the type limit."""
    limit = _f(ctx.thresholds["speed_limit_kmh"].get(ctx.machine_type))
    source = "machine_type"
    lat, lon = _f(row.get("gps_lat")), _f(row.get("gps_lon"))
    if lat is not None and lon is not None:
        for g in ctx.geofences:
            lim = _f(g.get("speed_limit_kmh"))
            poly = g.get("polygon") or {}
            coords = poly.get("coordinates") if isinstance(poly, Mapping) else None
            if lim is None or not coords:
                continue
            if _point_in_polygon(lon, lat, coords[0]) and (limit is None or lim < limit):
                limit, source = lim, f"geofence:{g.get('name') or g.get('id')}"
    return limit, source


def eval_overspeed(spec: Mapping[str, Any], row: Mapping[str, Any], ctx: Ctx) -> Reading:
    v = _f(row.get("ground_speed_kmh"))
    limit, source = speed_limit(row, ctx)
    inside = 1 - ctx.thresholds["hysteresis"]["inside_pct"] / 100
    over = v is not None and limit is not None and v > limit
    return Reading(
        levels={n: over for n in spec["levels"]},
        clear=v is None or limit is None or v < limit * inside,
        value=v,
        text={"limit": limit},
        evidence={"ground_speed_kmh": v, "limit_kmh": limit, "limit_source": source},
    )


def eval_hyd_pressure_drop(spec: Mapping[str, Any], row: Mapping[str, Any], ctx: Ctx) -> Reading:
    """§R HYD_PRESSURE_DROP, latched.

    Detection is the §R condition. A leak's pressure "falls and stays down", so after 3 more
    minutes the two windows would compare low with low and the condition would drop even though
    nothing is fixed. We therefore latch the baseline at detection: the condition stays true while
    the 2-min mean is below 65% of that baseline, and clears when it is back above 65% × 1.05.
    """
    ts = row["ts"]
    h = ctx.history
    sig = spec["signal"]
    recent = _mean(_window(h, ts, spec["recent_min"]), sig)
    base_end = ts - timedelta(minutes=spec["baseline_gap_min"])
    baseline = _mean(_window(h, base_end, spec["baseline_min"]), sig)
    load_rows = _window(h, ts, spec["load_min"])
    load_ok = bool(load_rows) and all(
        (_f(r.get("engine_load_pct")) or 0) > spec["load_pct"] for r in load_rows
    )
    # the 6-min load window must actually cover 6 min of data
    covered = (
        bool(load_rows) and (ts - load_rows[0]["ts"]).total_seconds() >= (spec["load_min"] - 1) * 60
    )
    speed = _mean(_window(h, ts, spec["stationary_min"]), "ground_speed_kmh")
    stationary = speed is not None and speed < spec["stationary_kmh"]
    inside = 1 + ctx.thresholds["hysteresis"]["inside_pct"] / 100

    st = ctx.state.setdefault("HYD_PRESSURE_DROP", {"baseline": None})
    detected = (
        recent is not None
        and baseline is not None
        and baseline > spec["min_baseline_bar"]
        and recent < spec["ratio"] * baseline
        and load_ok
        and covered
        and stationary
    )
    if detected and st["baseline"] is None:
        st["baseline"] = baseline
    latched = st["baseline"]
    cond = latched is not None and recent is not None and recent < spec["ratio"] * latched
    clear = latched is None or (recent is not None and recent >= spec["ratio"] * inside * latched)
    if latched is not None and clear:
        st["baseline"] = None  # released; the state machine still needs clear for 2 min
    ref = latched or baseline
    drop_pct = (1 - recent / ref) * 100 if recent is not None and ref else None
    return Reading(
        levels={n: cond for n in spec["levels"]},
        clear=clear,
        value=recent,
        worse=-1.0,
        text={"drop_pct": drop_pct},
        evidence={
            "hydraulic_pressure_bar_mean2": recent and round(recent, 1),
            "baseline_bar": ref and round(ref, 1),
            "drop_pct": drop_pct and round(drop_pct, 1),
        },
    )


EVALUATORS = {
    "threshold": eval_threshold,
    "tilt": eval_tilt,
    "seatbelt": eval_seatbelt,
    "excess_idle": eval_excess_idle,
    "overspeed": eval_overspeed,
    "hyd_pressure_drop": eval_hyd_pressure_drop,
}


# ---------------------------------------------------------------------------------------------
# Alert instances and events
# ---------------------------------------------------------------------------------------------
@dataclass
class AlertInstance:
    """One open (or just resolved) alert of one machine; becomes one `alerts` row."""

    key: str  # state key: alert_code, or FAULT_CODE:<code>
    alert_code: str
    machine_id: str
    site_id: str | None
    operator_id: str | None
    source: str  # alert_source enum
    category: str  # alert_category enum
    response: Response
    severity: str
    stage: str
    opened_ts: datetime
    stage_ts: datetime
    title: str
    message: str
    recommended_action: str | None
    evidence: dict[str, Any]
    anomaly_score: float | None = None
    resolved_ts: datetime | None = None
    acknowledged: bool = False
    db_id: int | None = None
    local_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # graded bookkeeping
    critical_held_s: float = -1.0
    critical_last_ts: datetime | None = None
    values: deque[tuple[datetime, float]] = field(default_factory=lambda: deque(maxlen=64))

    def row(self) -> dict[str, Any]:
        """`alerts` row (001_init.sql) for insert/update."""
        return {
            "ts": self.opened_ts.isoformat(),
            "site_id": self.site_id,
            "machine_id": self.machine_id,
            "operator_id": self.operator_id,
            "source": self.source,
            "category": self.category,
            "alert_code": self.alert_code,
            "title": self.title,
            "message": self.message,
            "recommended_action": self.recommended_action,
            "severity": self.severity,
            "stage": self.stage,
            "anomaly_score": self.anomaly_score,
            "evidence": self.evidence,
            "resolved_at": self.resolved_ts.isoformat() if self.resolved_ts else None,
        }

    def stream(self) -> dict[str, Any]:
        """`alert` WebSocket message data (api_contract.md)."""
        return {
            "id": self.db_id if self.db_id is not None else -1,
            "alert_code": self.alert_code,
            "severity": self.severity,
            "stage": self.stage,
            "title": self.title,
            "recommended_action": self.recommended_action,
        }

    def health_state(self) -> dict[str, Any]:
        """Open-alert dict in the shape `ml.inference.health.compute_health` accepts."""
        return {
            "alert_code": self.alert_code,
            "severity": self.severity,
            "stage": self.stage,
            "evidence": self.evidence,
        }


@dataclass
class AlertEvent:
    op: Literal["insert", "update"]
    reason: Literal["open", "stage", "severity", "resolve"]
    alert: AlertInstance
    row: dict[str, Any]  # snapshot of the alert row at this moment
    stream: dict[str, Any]  # snapshot of the WebSocket message data


@dataclass
class _Track:
    """Per rule key: how long each level's condition, and the clear band, have held."""

    held: dict[str, float] = field(default_factory=dict)  # level -> seconds (-1 = not true)
    clear_held: float = -1.0
    last_ts: datetime | None = None


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------------------------
# Per-machine engine
# ---------------------------------------------------------------------------------------------
class MachineRuleEngine:
    """Rules, hysteresis and graded response for one machine."""

    def __init__(
        self,
        machine_id: str,
        machine_type: str,
        site_id: str | None = None,
        thresholds: dict[str, Any] | None = None,
        geofences: list[dict[str, Any]] | None = None,
    ) -> None:
        self.machine_id = machine_id
        self.machine_type = machine_type
        self.site_id = site_id
        self.t = thresholds or load_thresholds()
        self.ctx = Ctx(
            machine_type=machine_type,
            history=deque(),
            thresholds=self.t,
            geofences=geofences or [],
            state={},
        )
        self.tracks: dict[str, _Track] = {}
        self.open: dict[str, AlertInstance] = {}
        self._prev_raw: dict[str, Any] | None = None
        self._prev_clean: dict[str, Any] | None = None
        self.operator_id: str | None = None

    # -- history -----------------------------------------------------------------------------
    def _clean(self, row: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        ts = row["ts"]
        contiguous = (
            self._prev_raw is not None
            and 0 < (ts - self._prev_raw["ts"]).total_seconds() <= CONTIGUOUS_S
        )
        glitch = (
            is_glitch(row, self._prev_raw, contiguous) if self.t.get("glitch_cleaning") else None
        )
        clean = dict(row)
        if glitch and self._prev_clean is not None:
            clean[glitch] = self._prev_clean.get(glitch)
        self._prev_raw, self._prev_clean = row, clean
        return clean, glitch

    def _remember(self, clean: dict[str, Any]) -> None:
        h = self.ctx.history
        h.append(clean)
        cutoff = clean["ts"] - timedelta(minutes=HISTORY_MIN)
        while h and h[0]["ts"] <= cutoff:
            h.popleft()

    def prime(self, row: dict[str, Any]) -> None:
        """Feed a warm-up row (before the replay start): history only, no alerts."""
        clean, _ = self._clean(row)
        self._remember(clean)

    # -- main entry ----------------------------------------------------------------------------
    def process(self, row: dict[str, Any]) -> list[AlertEvent]:
        """Evaluate every rule on one telemetry row (ts must be a tz-aware datetime)."""
        if row.get("operator_id"):
            self.operator_id = row["operator_id"]
        clean, glitch = self._clean(row)
        self._remember(clean)
        ts = clean["ts"]
        period = _f(clean.get("_period_s")) or DEFAULT_PERIOD_S
        events: list[AlertEvent] = []
        for code, spec in self.t["rules"].items():
            if spec["kind"] == "fault_code":
                events += self._fault_codes(spec, clean, ts, period)
                continue
            reading = EVALUATORS[spec["kind"]](spec, clean, self.ctx)
            if glitch:
                reading.evidence["glitch_ignored"] = glitch
            events += self.observe(code, code, spec, reading, ts, period)
        return events

    def _fault_codes(
        self, spec: Mapping[str, Any], row: Mapping[str, Any], ts: datetime, period: float
    ) -> list[AlertEvent]:
        table = self.t["fault_codes"]
        now = row.get("fault_code") or None
        keys = {k for k in self.open if k.startswith("FAULT_CODE:")} | {
            k for k in self.tracks if k.startswith("FAULT_CODE:")
        }
        if now:
            keys.add(f"FAULT_CODE:{now}")
        events: list[AlertEvent] = []
        for key in sorted(keys):
            code = key.split(":", 1)[1]
            info = table.get(
                code,
                {
                    "description": "unknown fault",
                    "severity": "warning",
                    "action": "Tell maintenance.",
                },
            )
            sev = info["severity"]
            present = now == code
            reading = Reading(
                levels={lvl: present and lvl == sev for lvl in spec["levels"]},
                clear=not present,
                text={"code": code, "description": info["description"], "action": info["action"]},
                evidence={"fault_code": code, "description": info["description"]},
            )
            events += self.observe(key, "FAULT_CODE", spec, reading, ts, period)
        return events

    # -- anomaly model -------------------------------------------------------------------------
    def observe_anomaly(self, result: Mapping[str, Any] | None, ts: datetime) -> list[AlertEvent]:
        """Turn one minute of model-1 output into UNUSUAL_BEHAVIOUR / SENSOR_GLITCH alerts."""
        if not result:
            return []
        kind = result.get("kind")
        top = result.get("top_signals") or []
        label = _signal_label(top[0]["feature"]) if top else "machine behaviour"
        score = _f(result.get("anomaly_score"))
        events: list[AlertEvent] = []
        for code, active in (
            ("UNUSUAL_BEHAVIOUR", kind == "machine_fault"),
            ("SENSOR_GLITCH", kind == "sensor_glitch"),
        ):
            cfg = self.t["anomaly"][code]
            spec = {
                "levels": {cfg["severity"]: {}},
                "category": "behaviour",
                "response": "none",
                "source": "anomaly_model",
                "title": cfg["title"],
                "actions": {"warn": cfg["action"]},
            }
            reading = Reading(
                levels={cfg["severity"]: active},
                clear=not active,
                value=score,
                text={"label": label},
                evidence={"kind": kind, "anomaly_score": score, "top_signals": top},
            )
            events += self.observe(
                code, code, spec, reading, ts, DEFAULT_PERIOD_S, anomaly_score=score
            )
        return events

    def acknowledge(self, db_id: int) -> None:
        for a in self.open.values():
            if a.db_id == db_id:
                a.acknowledged = True

    def open_alerts(self) -> list[AlertInstance]:
        return list(self.open.values())

    # -- state machine -------------------------------------------------------------------------
    def _hold_s(self, spec: Mapping[str, Any], level: str) -> float:
        lv = spec["levels"].get(level) or {}
        if "hold_s" in lv:
            return float(lv["hold_s"])
        return float(lv.get("hold_min", 0)) * 60

    def observe(
        self,
        key: str,
        alert_code: str,
        spec: Mapping[str, Any],
        r: Reading,
        ts: datetime,
        period: float,
        anomaly_score: float | None = None,
    ) -> list[AlertEvent]:
        tr = self.tracks.setdefault(key, _Track())
        step = (ts - tr.last_ts).total_seconds() if tr.last_ts else 0.0
        step = step if 0 < step <= CONTIGUOUS_S else 0.0
        tr.last_ts = ts

        # level holds
        for lvl in spec["levels"]:
            if r.levels.get(lvl):
                prev = tr.held.get(lvl, -1.0)
                tr.held[lvl] = prev + step if prev >= 0 and step > 0 else 0.0
            else:
                tr.held[lvl] = -1.0
        active = [
            lvl
            for lvl in spec["levels"]
            if tr.held.get(lvl, -1) >= 0 and tr.held[lvl] + period >= self._hold_s(spec, lvl)
        ]
        sev = max(active, key=SEVERITY_RANK.__getitem__) if active else None

        inst = self.open.get(key)
        events: list[AlertEvent] = []
        if sev is not None:
            tr.clear_held = -1.0
            if inst is None:
                inst = self._open(key, alert_code, spec, r, sev, ts, anomaly_score)
                self.open[key] = inst
                events.append(self._event("insert", "open", inst))
            else:
                self._refresh(inst, spec, r, anomaly_score)
                if SEVERITY_RANK[sev] > SEVERITY_RANK[inst.severity]:
                    inst.severity = sev
                    inst.evidence["severity_changes"] = inst.evidence.get(
                        "severity_changes", []
                    ) + [{"severity": sev, "ts": _iso(ts)}]
                    self._set_text(inst, spec, r)
                    events.append(self._event("update", "severity", inst))
            if r.value is not None:
                inst.values.append((ts, r.value))
            events += self._progress(inst, spec, r, sev, ts, step, period)
        elif inst is not None:
            inst.critical_last_ts = None  # pause graded timing while not critical
            if r.clear:
                tr.clear_held = tr.clear_held + step if tr.clear_held >= 0 and step > 0 else 0.0
                if tr.clear_held + period >= self.t["hysteresis"]["clear_hold_min"] * 60:
                    events.append(self._resolve(inst, spec, ts))
                    tr.clear_held = -1.0
            else:
                tr.clear_held = -1.0
        # drop idle FAULT_CODE tracks
        if (
            key.startswith("FAULT_CODE:")
            and key not in self.open
            and not any(h >= 0 for h in tr.held.values())
        ):
            self.tracks.pop(key, None)
        return events

    def _open(
        self,
        key: str,
        alert_code: str,
        spec: Mapping[str, Any],
        r: Reading,
        sev: str,
        ts: datetime,
        anomaly_score: float | None,
    ) -> AlertInstance:
        inst = AlertInstance(
            key=key,
            alert_code=alert_code,
            machine_id=self.machine_id,
            site_id=self.site_id,
            operator_id=self.operator_id,
            source=spec.get("source", "rule"),
            category=spec["category"],
            response=spec["response"],
            severity=sev,
            stage="warn",
            opened_ts=ts,
            stage_ts=ts,
            title="",
            message="",
            recommended_action=None,
            evidence={**r.evidence, "stages": [{"stage": "warn", "ts": _iso(ts)}]},
            anomaly_score=anomaly_score,
        )
        self._set_text(inst, spec, r)
        return inst

    def _refresh(
        self, inst: AlertInstance, spec: Mapping[str, Any], r: Reading, score: float | None
    ) -> None:
        keep = {k: inst.evidence[k] for k in ("stages", "severity_changes") if k in inst.evidence}
        inst.evidence = {**r.evidence, **keep}
        if score is not None:
            inst.anomaly_score = score

    def _set_text(self, inst: AlertInstance, spec: Mapping[str, Any], r: Reading) -> None:
        vals = {"value": r.value, **r.text}
        inst.title = _fmt(spec["title"], **vals)
        inst.recommended_action = _fmt(self._action(spec, inst.response, inst.stage), **vals)
        inst.message = self._message(inst, r)

    def _action(self, spec: Mapping[str, Any], response: str, stage: str) -> str:
        own = spec.get("actions", {})
        if stage in own:
            return own[stage]
        shared = (
            self.t.get(response, {}).get("actions", {})
            if response in ("graded", "escalate")
            else {}
        )
        if stage in shared:
            return shared[stage].replace("{warn}", own.get("warn", "")).strip()
        return own.get("warn", "")

    def _message(self, inst: AlertInstance, r: Reading) -> str:
        ev = r.evidence
        if "signal" in ev and ev.get("value") is not None:
            return f"{ev['signal']} = {ev['value']:.1f} (limit {ev['limit']}) on {self.machine_id}."
        details = ", ".join(
            f"{k}={v}" for k, v in ev.items() if v is not None and k != "top_signals"
        )
        return (
            f"{inst.alert_code} on {self.machine_id}: {details}."
            if details
            else f"{inst.alert_code} on {self.machine_id}."
        )

    def _rising(
        self, inst: AlertInstance, spec: Mapping[str, Any], r: Reading, ts: datetime
    ) -> bool:
        delta = _f(spec.get("rising_delta"))
        if delta is None or r.value is None:
            return False
        start = ts - timedelta(minutes=float(spec.get("rising_window_min", 3)))
        past = [v for t, v in inst.values if t <= start]
        if not past:
            return False
        return (r.value - past[-1]) * r.worse >= delta

    def _progress(
        self,
        inst: AlertInstance,
        spec: Mapping[str, Any],
        r: Reading,
        sev: str,
        ts: datetime,
        step: float,
        period: float,
    ) -> list[AlertEvent]:
        if inst.response == "none" or sev != "critical":
            inst.critical_last_ts = None
            return []
        # accumulate time spent critical (paused while not critical, restarts nothing)
        if inst.critical_held_s < 0:
            inst.critical_held_s = 0.0
        elif inst.critical_last_ts is not None and step > 0:
            inst.critical_held_s += step
        inst.critical_last_ts = ts
        # stage timings are elapsed data time since the alert went critical (warn at T,
        # derate at T+2, ...), unlike level holds where each sample covers its own period
        held = inst.critical_held_s
        cur = STAGE_RANK[inst.stage]
        target = cur
        if inst.response == "graded":
            g = self.t["graded"]
            stage_age = (ts - inst.stage_ts).total_seconds()
            if held >= g["derate_after_min"] * 60:
                target = max(target, STAGE_RANK["derate"])
            if held >= g["recommend_shutdown_after_min"] * 60 or (
                inst.stage == "derate"
                and stage_age >= g["rising_min_derate_min"] * 60
                and self._rising(inst, spec, r, ts)
            ):
                target = max(target, STAGE_RANK["recommend_shutdown"])
            if held >= g["escalate_after_min"] * 60 or (
                inst.stage == "recommend_shutdown"
                and not inst.acknowledged
                and stage_age >= g["ignored_after_min"] * 60
            ):
                target = max(target, STAGE_RANK["escalated"])
        else:  # escalate
            if held >= float(spec.get("escalate_after_min", 0)) * 60:
                target = STAGE_RANK["escalated"]
        if target <= cur:
            return []
        g = self.t["graded"]
        # never skip a stage silently: record each intermediate stage at this ts
        # graded: every intermediate stage; escalate-only: warn -> escalated directly
        passed = (
            STAGE_ORDER[cur + 1 : target + 1]
            if inst.response == "graded"
            else [STAGE_ORDER[target]]
        )
        for st in passed:
            entry: dict[str, Any] = {
                "stage": st,
                "ts": _iso(ts),
                "critical_min": round(held / 60, 1),
            }
            if inst.response == "graded":
                if st == "recommend_shutdown" and held < g["recommend_shutdown_after_min"] * 60:
                    entry["why"] = "value rising"
                if st == "escalated" and held < g["escalate_after_min"] * 60:
                    entry["why"] = "not acknowledged"
            inst.evidence["stages"].append(entry)
        inst.stage = STAGE_ORDER[target]
        inst.stage_ts = ts
        if inst.stage == "escalated":
            inst.severity = "critical"  # shows on the manager feed as critical
        self._set_text(inst, spec, r)
        return [self._event("update", "stage", inst)]

    def _resolve(self, inst: AlertInstance, spec: Mapping[str, Any], ts: datetime) -> AlertEvent:
        inst.stage = "resolved"
        inst.stage_ts = ts
        inst.resolved_ts = ts
        inst.evidence["stages"].append({"stage": "resolved", "ts": _iso(ts)})
        inst.evidence["time_to_resolve_min"] = round((ts - inst.opened_ts).total_seconds() / 60, 1)
        action = self._action(
            spec, inst.response if inst.response != "none" else "graded", "resolved"
        )
        inst.recommended_action = action or "Back to normal."
        self.open.pop(inst.key, None)
        return self._event("update", "resolve", inst)

    def _event(
        self, op: Literal["insert", "update"], reason: Any, inst: AlertInstance
    ) -> AlertEvent:
        return AlertEvent(op=op, reason=reason, alert=inst, row=inst.row(), stream=inst.stream())


def _signal_label(feature: str) -> str:
    labels = {
        "engine_rpm": "engine speed",
        "engine_load_pct": "engine load",
        "coolant_temp_c": "coolant temperature",
        "oil_pressure_kpa": "oil pressure",
        "hydraulic_pressure_bar": "hydraulic pressure",
        "hydraulic_oil_temp_c": "hydraulic oil temperature",
        "fuel_rate_lph": "fuel use",
        "battery_voltage": "battery voltage",
        "vibration_rms_g": "vibration",
        "pitch_deg": "pitch",
        "roll_deg": "roll",
        "ground_speed_kmh": "ground speed",
        "idle_pct30": "idling",
        "rpm_per_load": "engine speed for the load",
        "fuel_per_load": "fuel use for the load",
        "coolant_minus_hyd_oil_c": "coolant vs hydraulic oil temperature",
    }
    for suf in ("_mean5", "_std5", "_slope15"):
        if feature.endswith(suf):
            feature = feature[: -len(suf)]
    return labels.get(feature, feature.replace("_", " "))
