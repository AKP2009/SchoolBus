"""Build the web app's mock JSON (web/src/mocks/*.json) from the loaded data.

    python scripts/build_mocks.py                      # Supabase via DATABASE_URL in data/.env
    python scripts/build_mocks.py --source files       # the generator's data/output/ (same rows)

Every file matches a shape in docs/api_contract.md (Supabase rows use the column names of
supabase/migrations/001_init.sql). Nothing is invented where the data or a model can answer:

* Rows (shifts, tasks, telemetry, safety events, fatigue, incidents, training) come from the
  database, or from data/output/ restricted to the same window load_to_supabase.py loads
  (the last 14 shift dates), so both sources give the same mocks.
* Task times: ml.inference.task_time (LightGBM p10/p50/p90 + SHAP factors).
* Failure risk: ml.inference.maintenance (XGBoost on hourly features).
* Alerts, health and the WebSocket stream: the backend replay engine itself
  (backend/app/replay), run offline on the telemetry with an in-memory store, with the
  `overheating` scenario triggered on the demo machine exactly as POST /scenario does.
* Plan re-evaluation: ml.inference.plan with the triggers that hold at `now`.
* Clusters: ml/artifacts/clustering/ (fleet_metrics_weekly.csv and pca_points.json).
* Scenario quiz: backend/kb/scenarios.json (written into TM-SIM-01.scenario by the loader).
* Chat examples: the KB section each rag_eval.json question points at (the cached demo answers).

Two things the pipeline can't produce yet are built here and labelled as such in `_meta`:
the handover summary (LLM not wired; a template over the same inputs as models.md §9) and the
geofences (none are loaded; four zones placed from the site's own GPS extent).
Vision events in the window (blindspot, fatigue) become alerts through the backend's own
`app.services.events.alert_spec`, resolving EXPIRE_S after the event as the live service does.

Never written: anomaly_label, anomaly_type (not even read) and operators.personality.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "backend")]

from app.alerts.rules import MachineRuleEngine, load_thresholds  # noqa: E402
from app.replay.engine import ReplayEngine  # noqa: E402
from app.replay.source import TELEMETRY_COLUMNS, normalize  # noqa: E402
from app.replay.writer import MemoryStore  # noqa: E402
from app.schemas.events import AlertEvent as VisionEvent  # noqa: E402
from app.services.events import EXPIRE_S, alert_spec  # noqa: E402
from ml.inference import anomaly as A  # noqa: E402
from ml.inference import health as H  # noqa: E402
from ml.inference import maintenance as M  # noqa: E402
from ml.inference import plan as P  # noqa: E402
from ml.inference import task_time as T  # noqa: E402

OUT = REPO / "web" / "src" / "mocks"
DATA = REPO / "data" / "output"
KB = REPO / "backend" / "kb"
CLUSTER_DIR = REPO / "ml" / "artifacts" / "clustering"

# ---------------------------------------------------------------------------------------------
# Demo world (docs/demo_script.md): the night shift of OP02 on the dozer M05 at site S1.
# The operator's name comes from `operators`; the manager is an app account (profiles), not data.
# ---------------------------------------------------------------------------------------------
DEMO_OPERATOR = "OP02"
DEMO_MACHINE = "M05"
DEMO_SHIFT = "SH-2026-08-19-M05-N"
DEMO_SITE = "S1"
STREAM_FROM = datetime(2026, 8, 19, 15, 15, tzinfo=UTC)
STREAM_MIN = 120
NOW = STREAM_FROM + timedelta(minutes=STREAM_MIN)  # the mocks' "now" = end of the stream
OVERHEAT_AT_MIN = 100  # overheating trigger: escalated at +12 min, still open at `now`
MANAGER = {"name": "Priya Menon", "email": "priya@demo.site", "role": "manager", "site_id": DEMO_SITE}  # docs/supabase.md §4
WINDOW_DAYS = 14  # load_to_supabase.py default
HISTORY_DAYS = 30  # maintenance baselines (engine hours t-336 .. t-72)
FORBIDDEN = {"anomaly_label", "anomaly_type", "personality"}

TEL_COLS = list(dict.fromkeys(TELEMETRY_COLUMNS + A.INPUT_COLUMNS + M.TELEMETRY_COLUMNS + H.RULE_INPUT_COLUMNS))
assert not FORBIDDEN & set(TEL_COLS)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def iso(ts: Any) -> str | None:
    if ts is None or (isinstance(ts, float) and math.isnan(ts)) or ts is pd.NaT:
        return None
    t = pd.Timestamp(ts)
    if pd.isna(t):
        return None
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(v: Any) -> Any:
    """JSON-safe value: NaN -> None, numpy -> python, timestamps -> ISO text."""
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items() if k not in FORBIDDEN}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, (pd.Timestamp, datetime)):
        return iso(v)
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else round(f, 4)
    if v is pd.NaT or v is pd.NA:
        return None
    return v


def records(df: pd.DataFrame, cols: list[str] | None = None) -> list[dict[str, Any]]:
    d = df if cols is None else df[cols]
    return [clean(r) for r in d.to_dict("records")]


def pg_array(v: Any) -> list[str]:
    """'{a,b}' (CSV) or a list (psycopg) -> list."""
    if isinstance(v, (list, tuple, np.ndarray)):
        return [str(x) for x in v]
    if not isinstance(v, str) or not v.strip("{}"):
        return []
    return [x.strip('"') for x in v.strip("{}").split(",")]


def parse_json(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in "[{":
        return json.loads(v)
    return None if isinstance(v, float) and math.isnan(v) else v


# ---------------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------------
TIME_COLS = {
    "shifts": ["start_time", "end_time", "handover_generated_at"],
    "tasks": ["scheduled_start", "actual_start", "actual_end"],
    "weather": ["ts"],
    "safety_events": ["ts"],
    "fatigue_log": ["ts"],
    "incidents": ["ts"],
    "maintenance_log": ["event_date"],
    "training_records": ["started_at", "completed_at"],
}
SELECT = {  # explicit columns: hidden fields are never read
    "operators": "operator_id,site_id,full_name,experience_years,certification_level,languages,"
    "preferred_shift,skill_score",
}


class FileSource:
    """data/output/ from the generator, cut to the window load_to_supabase.py loads."""

    name = "files"

    def __init__(self, data_dir: Path) -> None:
        self.dir = data_dir
        shifts = pd.read_csv(data_dir / "shifts.csv")
        dates = sorted(shifts.shift_date.unique())[-WINDOW_DAYS:]
        self.first_date = dates[0]
        self.window_shifts = set(shifts[shifts.shift_date.isin(dates)].shift_id)
        self.window_start = pd.Timestamp(self.first_date).tz_localize("Asia/Kolkata").tz_convert("UTC")

    def table(self, name: str) -> pd.DataFrame:
        cols = SELECT.get(name)
        df = pd.read_csv(self.dir / f"{name}.csv", usecols=cols.split(",") if cols else None)
        for c in TIME_COLS.get(name, []):
            if c in df:
                df[c] = pd.to_datetime(df[c], utc=True)
        if name in ("tasks", "fatigue_log"):
            df = df[df.shift_id.isin(self.window_shifts)]
        elif name in ("weather", "safety_events", "incidents"):
            df = df[df.ts >= self.window_start]
        return df.reset_index(drop=True)

    def telemetry(self, machine_ids: list[str], start: datetime, end: datetime, window: bool = True) -> pd.DataFrame:
        """window=False reads before the loaded window (maintenance baselines only)."""
        df = pd.read_parquet(
            self.dir / "telemetry.parquet",
            columns=TEL_COLS,
            filters=[("machine_id", "in", machine_ids)],
        )
        df["ts"] = pd.to_datetime(df.ts, utc=True)
        df = df[(df.ts > start) & (df.ts <= end)]
        if window:
            df = df[df.shift_id.isin(self.window_shifts)]
        return df.sort_values(["machine_id", "ts"], kind="stable").reset_index(drop=True)


class PgSource:
    """Supabase over DATABASE_URL (data/.env, the session pooler, as load_to_supabase.py)."""

    name = "supabase"

    def __init__(self, env_file: Path) -> None:
        import psycopg
        from dotenv import load_dotenv

        load_dotenv(env_file)
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise SystemExit(f"DATABASE_URL is not set: copy data/.env.example to {env_file}, or use --source files")
        self.conn = psycopg.connect(url, connect_timeout=15)
        # The loaded window starts at the first loaded shift date (load_to_supabase.py --days).
        first = self._df("select min(start_time) as t from shifts s where exists (select 1 from tasks t where t.shift_id = s.shift_id)").t.iloc[0]
        self.window_start = pd.Timestamp(first).tz_convert("UTC").normalize() - pd.Timedelta(hours=5, minutes=30)

    def _df(self, q: str, params: tuple = ()) -> pd.DataFrame:
        with self.conn.cursor() as cur:
            cur.execute(q, params)
            cols = [d.name for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)

    def table(self, name: str) -> pd.DataFrame:
        df = self._df(f"select {SELECT.get(name, '*')} from {name}")  # noqa: S608 - fixed names
        df = df.drop(columns=[c for c in FORBIDDEN if c in df])
        for c in TIME_COLS.get(name, []):
            if c in df:
                df[c] = pd.to_datetime(df[c], utc=True)
        for c in df.columns:  # numeric(…) arrives as Decimal
            if df[c].dtype == object and len(df) and type(df[c].dropna().iloc[0] if df[c].notna().any() else None).__name__ == "Decimal":
                df[c] = pd.to_numeric(df[c])
        if name in ("training_modules",):
            df["machine_types"] = df.machine_types.map(lambda v: "{" + ",".join(v or []) + "}")
            df["languages"] = df.languages.map(lambda v: "{" + ",".join(v or []) + "}")
        return df

    def telemetry(self, machine_ids: list[str], start: datetime, end: datetime, window: bool = True) -> pd.DataFrame:
        cols = ", ".join(TEL_COLS)
        df = self._df(
            f"select {cols} from telemetry where machine_id = any(%s) and ts > %s and ts <= %s order by machine_id, ts",  # noqa: S608
            (machine_ids, start, end),
        )
        df["ts"] = pd.to_datetime(df.ts, utc=True)
        for c in df.columns:
            if c not in ("ts", "machine_id", "operator_id", "shift_id", "fault_code", "seatbelt_fastened", "is_idle"):
                df[c] = pd.to_numeric(df[c])
        return df


class FrameTelemetry:
    """Replay-engine TelemetrySource over an in-memory frame (either source above)."""

    name = "parquet"

    def __init__(self, tel: pd.DataFrame) -> None:
        self.frames = {m: g.reset_index(drop=True) for m, g in tel.groupby("machine_id", sort=False)}

    def fetch(self, machine_id: str, after: datetime, until: datetime | None = None, limit: int = 1000):
        g = self.frames.get(machine_id)
        if g is None:
            return []
        i = int(g.ts.searchsorted(pd.Timestamp(after), side="right"))
        part = g.iloc[i : i + limit]
        if until is not None:
            part = part[part.ts <= pd.Timestamp(until)]
        return [normalize(r) for r in part[TELEMETRY_COLUMNS].to_dict("records")]


# ---------------------------------------------------------------------------------------------
# Plain-language labels (docs/design.md: say what to do, never raw codes)
# ---------------------------------------------------------------------------------------------
SIGNAL_WORD = {
    "coolant_temp_c": "Coolant temperature",
    "engine_oil_temp_c": "Engine oil temperature",
    "oil_pressure_kpa": "Engine oil pressure",
    "hydraulic_pressure_bar": "Hydraulic pressure",
    "hydraulic_oil_temp_c": "Hydraulic oil temperature",
    "battery_voltage": "Battery voltage",
    "vibration_rms_g": "Vibration",
    "fuel_rate_lph": "Fuel burn",
    "engine_rpm": "Engine speed",
    "engine_load_pct": "Engine load",
}
SIGNAL_WORD["vibration_travel_g"] = "Vibration while travelling"
AGG_WORD = {"mean": "average", "slope": "trend", "devz": "distance from this machine's normal",
            "dev": "distance from this machine's normal", "max": "peak", "std_mean": "variability"}
SPECIAL_WORD = {
    "worst_dev_z24": "Largest signal deviation, last 24 h",
    "worst_trend_z72": "Steepest signal trend, last 72 h",
    "age_years": "Machine age",
    "service_ratio": "Share of service interval used",
    "hours_since_service": "Hours since last service",
    "high_load_hours_since_service": "High-load hours since service",
    "fault_code_count24": "Fault codes, last 24 h",
    "fault_code_count7d": "Fault codes, last 7 days",
    "anomaly_count24": "Unusual-behaviour minutes, last 24 h",
    "anomaly_score_mean24": "Unusual-behaviour score, last 24 h",
}


def factor_label(feature: str) -> str:
    """Maintenance feature name -> plain words, e.g. 'hydraulic_oil_temp_c_slope24' ->
    'Hydraulic oil temperature trend, last 24 h'."""
    if feature in SPECIAL_WORD:
        return SPECIAL_WORD[feature]
    if feature.startswith("machine_type_"):
        return "Machine type: " + feature.removeprefix("machine_type_").replace("_", " ")
    for sig in sorted(SIGNAL_WORD, key=len, reverse=True):
        if feature.startswith(sig + "_"):
            m = re.fullmatch(r"(std_mean|mean|slope|devz|dev|max)(\d+)", feature[len(sig) + 1 :])
            if m:
                return f"{SIGNAL_WORD[sig]} {AGG_WORD[m.group(1)]}, last {m.group(2)} h"
            return SIGNAL_WORD[sig]
    return feature.replace("_", " ").capitalize()


# ---------------------------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------------------------
class Build:
    def __init__(self, src: Any) -> None:
        self.src = src
        log(f"reading tables from {src.name}")
        self.sites = src.table("sites")
        self.operators = src.table("operators")
        self.machines = src.table("machines")
        self.shifts = src.table("shifts")
        self.weather = src.table("weather")
        self.tasks = src.table("tasks")
        self.safety = src.table("safety_events")
        self.fatigue = src.table("fatigue_log")
        self.incidents = src.table("incidents")
        self.mlog = src.table("maintenance_log")
        self.modules = src.table("training_modules")
        self.records = src.table("training_records")
        assert not FORBIDDEN & set(self.operators.columns)
        self.op_name = self.operators.set_index("operator_id").full_name.to_dict()
        self.mtype = self.machines.set_index("machine_id").machine_type
        self.shift = self.shifts.set_index("shift_id").loc[DEMO_SHIFT]
        assert self.shift.operator_id == DEMO_OPERATOR and self.shift.machine_id == DEMO_MACHINE
        self.site_machines = sorted(self.machines[self.machines.site_id == DEMO_SITE].machine_id)
        self.window_start = getattr(src, "window_start", None)
        self.out: dict[str, Any] = {}
        self.meta_notes: list[str] = []
        self.alert_ids = iter(range(1, 10**6))

    # -- helpers -------------------------------------------------------------------------------
    def weather_at(self, ts: datetime) -> dict[str, float]:
        w = self.weather[(self.weather.site_id == DEMO_SITE) & (self.weather.ts <= ts)].sort_values("ts").iloc[-1]
        return {c: float(w[c]) for c in ("temp_c", "humidity_pct", "rain_mm", "wind_kmh", "visibility_m", "dust_index")} | {"ts": iso(w.ts)}

    # -- world ---------------------------------------------------------------------------------
    def world(self) -> None:
        site = self.sites.set_index("site_id").loc[DEMO_SITE]
        op = self.operators.set_index("operator_id").loc[DEMO_OPERATOR]
        m = self.machines.set_index("machine_id").loc[DEMO_MACHINE]
        self.out["world"] = clean({
            "now": NOW,
            "stream_from": STREAM_FROM,
            "stream_minutes": STREAM_MIN,
            "site": {"site_id": DEMO_SITE, "name": site["name"], "site_type": site.site_type, "lat": site.lat, "lon": site.lon, "timezone": site.timezone},
            "sites": records(self.sites, ["site_id", "name"]),
            "operator": {
                "operator_id": DEMO_OPERATOR,
                "full_name": op.full_name,
                "experience_years": op.experience_years,
                "certification_level": op.certification_level,
                "languages": pg_array(op.languages),
                "preferred_shift": op.preferred_shift,
            },
            "manager": MANAGER,
            "machine": {"machine_id": DEMO_MACHINE, "machine_type": m.machine_type, "model": m.model, "serial_no": m.serial_no, "year": m.year},
            "shift": {
                "shift_id": DEMO_SHIFT,
                "shift_type": self.shift.shift_type,
                "shift_date": self.shift.shift_date,
                "start_time": self.shift.start_time,
                "end_time": self.shift.end_time,
                "fuel_start_pct": self.shift.fuel_start_pct,
            },
            "weather": self.weather_at(NOW),
        })

    # -- task times ----------------------------------------------------------------------------
    def task_features(self) -> pd.DataFrame:
        art = T.load_artifacts()
        hist = self.tasks[(self.tasks.status == "completed") & (self.tasks.actual_end <= self.shift.start_time)]
        F = T.build_feature_table(self.tasks[self.tasks.shift_id == DEMO_SHIFT], self.weather, self.operators,
                                  self.machines, self.shifts, self.fatigue[self.fatigue.ts < self.shift.start_time],
                                  self.mlog, art["reference"], history=hist)
        return F

    def tasks_now(self) -> None:
        log("task time predictions")
        F = self.task_features()
        preds = T.predict_task_time(F)
        by = {p["task_id"]: p for p in preds}
        t = self.tasks[self.tasks.shift_id == DEMO_SHIFT].sort_values("sequence_no").copy()
        # status at `now` from the actual times; nothing after `now` leaks into the mocks
        done = t.actual_end.notna() & (t.actual_end <= NOW)
        started = t.actual_start.notna() & (t.actual_start <= NOW)
        t["status"] = np.select([done, started], ["completed", "in_progress"], "scheduled")
        t.loc[~done, ["actual_end", "actual_duration_min"]] = [pd.NaT, np.nan]
        t.loc[~started, "actual_start"] = pd.NaT
        t.loc[~done, "delay_reason"] = None
        t["predicted_p10_min"] = t.task_id.map(lambda i: by[i]["p10_min"])
        t["predicted_p50_min"] = t.task_id.map(lambda i: by[i]["p50_min"])
        t["predicted_p90_min"] = t.task_id.map(lambda i: by[i]["p90_min"])
        t["prediction_factors"] = t.task_id.map(lambda i: by[i]["factors"])
        t["updated_at"] = NOW
        self.task_rows = t
        self.out["tasks"] = records(t)
        self.out["task_predictions"] = {"predictions": clean(preds)}
        self.task_F = F

    # -- maintenance ---------------------------------------------------------------------------
    def maintenance(self) -> None:
        log("maintenance features (anomaly model on every minute, all machines)")
        # The model compares each signal with the machine's own baseline over engine hours
        # (t-336, t-72]: about three weeks. The loaded 14 days can't hold that, so the files source
        # reads HISTORY_DAYS back for this step only (Supabase gives whatever is loaded).
        hist_start = NOW - timedelta(days=HISTORY_DAYS)
        tel = self.src.telemetry(list(self.machines.machine_id), hist_start, NOW, window=False)
        self.tel_hist = tel[tel.ts >= self.window_start] if self.window_start is not None else tel
        if tel.ts.min() > hist_start + timedelta(days=2):
            self.meta_notes.append(f"maintenance: telemetry starts {iso(tel.ts.min())}; baselines need ~21 days, so failure probabilities are understated")
        else:
            self.meta_notes.append(f"maintenance: features from {HISTORY_DAYS} days of telemetry (baselines need ~21 days; the 14-day load has too little)")
        mscores = M.score_minutes(tel, self.mtype)
        feats = M.build_hourly_features(tel, mscores, self.shifts, self.machines, self.mlog)
        feats = feats[feats.ts <= NOW]
        latest = feats.sort_values("ts").groupby("machine_id").tail(1)
        preds = M.predict_failure(latest[[*M.KEY_COLUMNS, *M.SPEC_FEATURES, *M.RAW_EXTRA_COLUMNS]].reset_index(drop=True))
        version = json.loads((M.ARTIFACT_DIR / "metrics.json").read_text()).get("version", "v2")
        rows = []
        for i, p in enumerate(sorted(preds, key=lambda p: -p["failure_probability"]), start=1):
            ts = latest.set_index("machine_id").loc[p["machine_id"], "ts"]
            rows.append({
                "id": i,
                "machine_id": p["machine_id"],
                "predicted_at": iso(ts),
                "horizon_hours": p["horizon_hours"],
                "failure_probability": p["failure_probability"],
                "likely_component": p["likely_component"],
                "top_factors": [{**f, "label": factor_label(f["feature"])} for f in p["top_factors"]],
                "model_version": f"maintenance-{version}",
                "risk_band": p["risk_band"],
            })
        self.maint = {r["machine_id"]: r for r in rows}
        self.out["maintenance_predictions"] = clean(rows)

    # -- replay: alerts, health, stream --------------------------------------------------------
    async def replay(self) -> None:
        log("replaying the shift through the backend engine")
        active = sorted(self.shifts[(self.shifts.start_time <= NOW) & (self.shifts.end_time > STREAM_FROM)].machine_id)
        self.replay_machines = active
        start = self.shift.start_time.to_pydatetime()
        tel = self.tel_hist[(self.tel_hist.machine_id.isin(active)) & (self.tel_hist.ts > start - timedelta(hours=1)) & (self.tel_hist.ts <= NOW)]
        msgs: list[tuple[datetime, str, str, Any]] = []
        clock: dict[str, datetime] = {}

        class Pusher:
            async def send(self, machine_id: str, kind: str, data: Any) -> None:
                msgs.append((clock["t"], machine_id, kind, data))

        store = MemoryStore()
        store.maintenance = {m: {"failure_probability": r["failure_probability"], "likely_component": r["likely_component"]}
                             for m, r in self.maint.items()}
        eng = ReplayEngine(pusher=Pusher(), store=store, score=True)
        machines = {m: {"machine_id": m, "site_id": self.machines.set_index("machine_id").site_id[m], "machine_type": self.mtype[m]} for m in active}
        clock["t"] = start
        await eng.start(active, start, 1, FrameTelemetry(tel), machines, run_loop=False)
        t = start
        trigger = STREAM_FROM + timedelta(minutes=OVERHEAT_AT_MIN)
        while t < NOW:
            t = min(t + timedelta(seconds=30), NOW)
            clock["t"] = t
            if t >= trigger and not getattr(self, "_triggered", False):
                sc = eng.trigger("overheating", DEMO_MACHINE)
                self._triggered = True
                self.meta_notes.append(f"overheating scenario triggered on {DEMO_MACHINE} at {iso(sc.start_ts)} (POST /scenario/overheating)")
            await eng.advance_to(t)
            assert eng.writer is not None
            await eng.writer.flush()
        self.health_now = {m: eng.machine_health(m) for m in active}
        self.last_rows = {m: eng.streams[m].last_row for m in active}
        await eng.stop()
        self.store = store
        self.msgs = msgs
        self.alert_ids = iter(range(max(store.alerts, default=0) + 1, 10**6))

    # -- vision events -> alerts: the backend's own mapping (app.services.events) -----------
    def vision_alert(self, ev: pd.Series) -> dict[str, Any] | None:
        """alerts row for a vision safety event, as POST /events writes it (alert_spec). One
        event = one episode; it resolves EXPIRE_S after the event (SOS never auto-resolves)."""
        if ev.event_type not in ("proximity_breach", "blindspot_intrusion", "fatigue_high", "phone_use", "sos"):
            return None
        details = parse_json(ev.details) or {}
        try:
            e = VisionEvent.model_validate({
                "type": ev.event_type, "machine_id": ev.machine_id, "operator_id": ev.operator_id,
                "ts": iso(ev.ts), "severity": ev.severity,
                "distance_m": None if pd.isna(ev.distance_m) else float(ev.distance_m),
                "sector": ev.sector if isinstance(ev.sector, str) else None,
                "approaching": None if pd.isna(ev.approaching) else bool(ev.approaching),
                "details": {k: v for k, v in details.items() if k != "source"},
            })
        except Exception as err:  # noqa: BLE001 - a row /events would reject gets no alert
            log(f"    skipped vision event {ev.id}: {err}")
            return None
        spec = alert_spec(e, self.op_name.get(str(ev.operator_id)))
        resolves = None if ev.event_type == "sos" else ev.ts + timedelta(seconds=EXPIRE_S)
        return {
            "id": next(self.alert_ids),
            "ts": iso(ev.ts),
            "site_id": ev.site_id,
            "machine_id": ev.machine_id,
            "operator_id": ev.operator_id,
            **spec,
            "severity": "emergency" if ev.event_type == "sos" else ev.severity,
            "anomaly_score": None,
            "evidence": clean({"distance_m": ev.distance_m, "sector": e.sector.value if e.sector else None, "approaching": e.approaching, **details}),
            "acknowledged_by": None,
            "acknowledged_at": None,
            "resolved_at": iso(resolves) if resolves is not None and resolves <= NOW else None,
        }

    # -- alerts ----------------------------------------------------------------------------------
    def alerts(self) -> None:
        rows = [dict(a) for a in self.store.alerts.values()]
        for r in rows:
            r["ts"] = iso(r["ts"])
            r["resolved_at"] = iso(r["resolved_at"]) if r.get("resolved_at") else None
            r.setdefault("acknowledged_by", None)
            r.setdefault("acknowledged_at", None)
        # Vision events on the site this shift become alerts the way POST /events writes them.
        ev = self.safety[(self.safety.site_id == DEMO_SITE) & (self.safety.ts >= self.shift.start_time) & (self.safety.ts <= NOW)]
        self.vision_rows = []
        for _, e in ev.sort_values("ts").iterrows():
            a = self.vision_alert(e)
            if a is None:
                continue
            self.vision_rows.append((e, a))
            rows.append(a)
        self.all_alerts = rows
        open_rows = sorted([r for r in rows if r["resolved_at"] is None], key=lambda r: r["ts"], reverse=True)
        self.out["alerts_open"] = clean(open_rows)
        for r in sorted(rows, key=lambda r: r["ts"]):
            log(f"    alert {r['id']} {r['ts']} {r['machine_id']} {r['alert_code']} {r['severity']} {r['stage']} resolved={r['resolved_at']}")
        sev = {r["severity"] for r in open_rows}
        esc = [r for r in open_rows if r["alert_code"].startswith("COOLANT") and r["stage"] == "escalated"]
        log(f"open alerts: {len(open_rows)} severities={sorted(sev)} escalated overheating={len(esc)}")
        if not esc:
            raise SystemExit("expected an escalated overheating alert at `now`; check OVERHEAT_AT_MIN")

    # -- health ----------------------------------------------------------------------------------
    def health(self) -> None:
        log("health for every machine")
        out = []
        for m in sorted(self.machines.machine_id):
            h = self.health_now.get(m)
            if h is None:  # not on shift now: score its last recorded minute
                g = self.tel_hist[self.tel_hist.machine_id == m]
                if g.empty:
                    continue
                w = g.tail(60)
                anomaly = A.score_anomaly(w[A.INPUT_COLUMNS], machine_type=self.mtype[m])
                rules = H.rule_states_frame(w).rule_states.iloc[-1]
                mp = self.maint.get(m)
                h = H.compute_health(m, w.ts.iloc[-1], rules, anomaly,
                                     {"failure_probability": mp["failure_probability"], "likely_component": mp["likely_component"]} if mp else None)
            out.append({"machine_id": m, **{k: v for k, v in h.items() if k != "machine_id"}})
        self.health_rows = {h["machine_id"]: h for h in out}
        self.out["machine_health"] = clean(out)

    # -- stream ----------------------------------------------------------------------------------
    def stream(self) -> None:
        log("telemetry stream")
        msgs = []
        for t, m, kind, data in self.msgs:
            if m != DEMO_MACHINE or t <= STREAM_FROM:
                continue
            ts = pd.Timestamp(data["ts"]) if kind == "telemetry" else pd.Timestamp(t)
            msgs.append((ts, {"kind": kind, "data": clean(data)}))
        # vision: safety messages for the demo machine, alert messages for its vision alerts
        ev = self.safety[(self.safety.machine_id == DEMO_MACHINE) & (self.safety.ts > STREAM_FROM) & (self.safety.ts <= NOW)]
        for _, e in ev.iterrows():
            msgs.append((e.ts, {"kind": "safety", "data": clean({"type": e.event_type, "severity": e.severity, "distance_m": e.distance_m, "sector": e.sector if isinstance(e.sector, str) else None, "approaching": e.approaching if pd.notna(e.approaching) else None})}))
        for e, a in getattr(self, "vision_rows", []):
            if e.machine_id != DEMO_MACHINE or e.ts <= STREAM_FROM:
                continue
            base = {k: a[k] for k in ("id", "alert_code", "severity", "stage", "title", "recommended_action")}
            msgs.append((e.ts, {"kind": "alert", "data": base}))
            if a["resolved_at"]:
                msgs.append((pd.Timestamp(a["resolved_at"]), {"kind": "alert", "data": {**base, "stage": "resolved"}}))
        # fatigue once a minute (the vision service's fatigue_sample)
        f = self.fatigue[(self.fatigue.operator_id == DEMO_OPERATOR) & (self.fatigue.ts > STREAM_FROM) & (self.fatigue.ts <= NOW)]
        for _, r in f.iterrows():
            msgs.append((r.ts, {"kind": "fatigue", "data": {"fatigue_level": r.fatigue_level, "fatigue_score": clean(r.fatigue_score)}}))
        msgs.sort(key=lambda x: (x[0], ["telemetry", "safety", "fatigue", "health", "alert"].index(x[1]["kind"])))
        self.out["telemetry_stream"] = {
            "machine_id": DEMO_MACHINE,
            "from": iso(STREAM_FROM),
            "to": iso(NOW),
            "note": "One WebSocket message per entry; `at_s` = data seconds after `from`. Replay at `speed` data seconds per real second.",
            "messages": [{"at_s": round((ts - pd.Timestamp(STREAM_FROM)).total_seconds(), 1), **m} for ts, m in msgs],
        }
        counts = pd.Series([m["kind"] for _, m in msgs]).value_counts().to_dict()
        log(f"stream: {len(msgs)} messages {counts}")

    # -- live snapshot for the operator (last telemetry, safety state) -------------------------
    def machine_live(self) -> None:
        r = self.last_rows.get(DEMO_MACHINE) or {}
        self.out["machine_live"] = clean({"machine_id": DEMO_MACHINE, **{k: v for k, v in r.items() if not str(k).startswith("_")}})
        # 60-minute signal history per subsystem for the digital twin drawer
        g = self.tel_hist[(self.tel_hist.machine_id == DEMO_MACHINE) & (self.tel_hist.ts > NOW - timedelta(minutes=60))]
        stream_tel = [m["data"] for m in self.out["telemetry_stream"]["messages"] if m["kind"] == "telemetry"]
        s = pd.DataFrame(stream_tel)
        s["ts"] = pd.to_datetime(s.ts, utc=True)
        s = s[s.ts > NOW - timedelta(minutes=60)]
        s = s.set_index("ts").resample("1min").last().ffill().reset_index()
        hist = s if len(s) >= 30 else g
        cols = ["engine_rpm", "oil_pressure_kpa", "coolant_temp_c", "engine_oil_temp_c", "hydraulic_oil_temp_c", "hydraulic_pressure_bar", "battery_voltage", "vibration_rms_g"]
        self.out["machine_signals"] = {"machine_id": DEMO_MACHINE, "to": iso(NOW), "minutes": 60,
                                       "series": {c: [clean(v) for v in hist[c].tolist()] for c in cols if c in hist}}

    # -- handover ------------------------------------------------------------------------------
    def handover(self) -> None:
        log("handover")
        prev = self.shifts[(self.shifts.machine_id == DEMO_MACHINE) & (self.shifts.end_time <= self.shift.start_time)].sort_values("start_time").iloc[-1]
        unfinished = self.tasks[(self.tasks.shift_id == prev.shift_id) & (self.tasks.status.isin(["delayed", "scheduled", "in_progress"]))]
        ev = self.safety[(self.safety.machine_id == DEMO_MACHINE) & (self.safety.ts >= prev.start_time) & (self.safety.ts < prev.end_time)]
        mp = self.maint.get(DEMO_MACHINE)
        # rule-engine alerts of the previous shift (the backend would have written them)
        prev_alerts = self.rule_alerts(DEMO_MACHINE, prev.start_time, prev.end_time)
        comp = mp["likely_component"] if mp else None
        cond = (f"Failure risk {round(mp['failure_probability'] * 100)}% in the next 48 engine hours, most likely {comp}."
                if mp and mp["failure_probability"] >= 0.3 else "Machine condition normal; no failure risk flagged.")
        issues = [a["title"] for a in prev_alerts][:2]
        notes = str(prev.handover_notes or "").strip()
        open_line = ("Last shift: " + "; ".join(issues) + ".") if issues else f"Last shift noted: {notes}" if notes else "No open issues from last shift."
        ev_counts = ev.event_type.value_counts()
        check = "Walk around and check the rear side: people were near the machine." if "people_near_machine" in pg_array(prev.issues_reported) or ev_counts.get("proximity_breach", 0) + ev_counts.get("blindspot_intrusion", 0) else "Do the usual walk-around and check fluid levels."
        work = "; ".join(f"Task {int(r.sequence_no)} ({r.task_type} {r.material_type}) not finished" for _, r in unfinished.iterrows()) or "All tasks finished."
        fuel = f"Fuel {prev.fuel_end_pct:.0f}% at handover" + (" — refuel before starting." if prev.fuel_end_pct < 40 else ".")
        summary = "\n".join([cond, open_line, check, work, fuel])
        self.out["handover"] = clean({
            "shift_id": prev.shift_id,
            "machine_id": DEMO_MACHINE,
            "operator_id": prev.operator_id,
            "operator_name": self.op_name.get(prev.operator_id),
            "shift_type": prev.shift_type,
            "start_time": prev.start_time,
            "end_time": prev.end_time,
            "fuel_start_pct": prev.fuel_start_pct,
            "fuel_end_pct": prev.fuel_end_pct,
            "handover_notes": prev.handover_notes,
            "issues_reported": pg_array(prev.issues_reported),
            "handover_summary": summary,
            "handover_generated_at": self.shift.start_time,
            "unfinished_tasks": records(unfinished, ["task_id", "sequence_no", "task_type", "material_type", "quantity", "unit", "status"]),
            "alerts": prev_alerts,
            "safety_event_count": int(len(ev)),
        })
        self.meta_notes.append("handover_summary: template over the models.md §9 inputs (LLM not wired yet)")

    # -- rule engine over history (machine logs) ------------------------------------------------
    def rule_alerts(self, machine_id: str, start: Any, end: Any) -> list[dict[str, Any]]:
        g = self.tel_hist[(self.tel_hist.machine_id == machine_id) & (self.tel_hist.ts >= start) & (self.tel_hist.ts < end)]
        if g.empty:
            return []
        m = self.machines.set_index("machine_id").loc[machine_id]
        eng = MachineRuleEngine(machine_id, m.machine_type, m.site_id, self._thr, [])
        seen: dict[str, dict[str, Any]] = {}
        rank = {"info": 0, "warning": 1, "critical": 2, "emergency": 3}
        stages = ["warn", "derate", "recommend_shutdown", "escalated", "resolved"]
        for r in g[TELEMETRY_COLUMNS].to_dict("records"):
            row = {**normalize(r), "_period_s": 60.0}
            for ev in eng.process(row):
                a = ev.alert
                cur = seen.setdefault(a.local_id, {"alert_code": a.alert_code, "title": a.title, "severity": a.severity,
                                                   "max_stage": a.stage, "ts": iso(a.opened_ts), "resolved_at": None})
                if rank[a.severity] > rank[cur["severity"]]:
                    cur["severity"], cur["title"] = a.severity, a.title
                if a.stage == "resolved":
                    cur["resolved_at"] = iso(a.resolved_ts or row["ts"])
                elif stages.index(a.stage) > stages.index(cur["max_stage"]):
                    cur["max_stage"] = a.stage
        return sorted(seen.values(), key=lambda x: x["ts"])

    def machine_logs(self) -> None:
        log("7-day machine logs")
        start = NOW - timedelta(days=7)
        sh = self.shifts[(self.shifts.machine_id == DEMO_MACHINE) & (self.shifts.start_time >= start) & (self.shifts.start_time < NOW)].sort_values("start_time", ascending=False)
        out = []
        for _, s in sh.iterrows():
            end = min(s.end_time, pd.Timestamp(NOW))
            ev = self.safety[(self.safety.machine_id == DEMO_MACHINE) & (self.safety.ts >= s.start_time) & (self.safety.ts < end)]
            inc = self.incidents[(self.incidents.machine_id == DEMO_MACHINE) & (self.incidents.ts >= s.start_time) & (self.incidents.ts < end)]
            tk = self.tasks[self.tasks.shift_id == s.shift_id]
            live = s.shift_id == DEMO_SHIFT
            out.append({
                "shift_id": s.shift_id,
                "shift_type": s.shift_type,
                "operator_id": s.operator_id,
                "operator_name": self.op_name.get(s.operator_id),
                "start_time": iso(s.start_time),
                "end_time": iso(s.end_time),
                "in_progress": bool(live),
                "fuel_start_pct": clean(s.fuel_start_pct),
                "fuel_end_pct": None if live else clean(s.fuel_end_pct),
                "handover_notes": None if live else s.handover_notes,
                "issues_reported": [] if live else pg_array(s.issues_reported),
                "tasks_completed": int(((tk.status == "completed") & (tk.actual_end <= end)).sum()),
                "tasks_total": int(len(tk)),
                "alerts": self.rule_alerts(DEMO_MACHINE, s.start_time, end) if not live else [
                    {"alert_code": a["alert_code"], "title": a["title"], "severity": a["severity"], "max_stage": a["stage"], "ts": a["ts"], "resolved_at": a["resolved_at"]}
                    for a in self.all_alerts if a["machine_id"] == DEMO_MACHINE],
                "safety_events": clean(ev.event_type.value_counts().to_dict()),
                "incidents": records(inc, ["id", "ts", "incident_type", "severity", "description", "status"]),
            })
        self.out["machine_logs"] = {"machine_id": DEMO_MACHINE, "from": iso(start), "to": iso(NOW), "shifts": out}

    # -- safety / fatigue / incidents ----------------------------------------------------------
    def safety_data(self) -> None:
        ev = self.safety[(self.safety.site_id == DEMO_SITE) & (self.safety.ts <= NOW)].copy()
        ev["details"] = ev.details.map(parse_json)
        ev["alert_id"] = ev.alert_id.map(lambda v: None if pd.isna(v) else int(v))
        self.out["safety_events"] = records(ev.sort_values("ts", ascending=False))
        sh = self.shifts[(self.shifts.site_id == DEMO_SITE) & (self.shifts.start_time <= NOW)]
        if self.window_start is not None:
            sh = sh[sh.end_time >= self.window_start]
        self.out["shifts"] = records(sh, ["shift_id", "operator_id", "machine_id", "shift_type", "shift_date", "start_time", "end_time"])
        f = self.fatigue[(self.fatigue.shift_id == DEMO_SHIFT) & (self.fatigue.ts <= NOW)].sort_values("ts")
        self.out["fatigue"] = records(f, ["ts", "operator_id", "shift_id", "ear_avg", "perclos_60s", "yawn_count", "head_down_events", "phone_detected", "fatigue_score", "fatigue_level"])
        assert (f.fatigue_level == "high").any(), "fatigue series should reach high"
        inc = self.incidents[(self.incidents.site_id == DEMO_SITE) & (self.incidents.ts <= NOW)].sort_values("ts", ascending=False).copy()
        inc["media_paths"] = inc.media_paths.map(pg_array)
        for c in ("linked_alert_id", "linked_event_id"):
            inc[c] = inc[c].map(lambda v: None if pd.isna(v) else int(v)).astype(object)
        self.out["incidents"] = records(inc)

    # -- fleet map -----------------------------------------------------------------------------
    def fleet(self) -> None:
        log("fleet map and geofences")
        latest = self.tel_hist[self.tel_hist.ts <= NOW].groupby("machine_id").tail(1).set_index("machine_id")
        for m, r in self.last_rows.items():  # scenario rows included
            if r:
                latest.loc[m, ["ts", "gps_lat", "gps_lon", "fuel_level_pct", "ground_speed_kmh", "is_idle", "operator_id", "shift_id", "coolant_temp_c"]] = [
                    pd.Timestamp(r["ts"]), r["gps_lat"], r["gps_lon"], r["fuel_level_pct"], r["ground_speed_kmh"], r["is_idle"], r["operator_id"], r["shift_id"], r["coolant_temp_c"]]
        rank = {"info": 0, "warning": 1, "critical": 2, "emergency": 3}
        out = []
        for _, m in self.machines.iterrows():
            L = latest.loc[m.machine_id] if m.machine_id in latest.index else None
            on_shift = L is not None and pd.Timestamp(L.ts) >= pd.Timestamp(NOW) - timedelta(minutes=5)
            alerts = [a for a in self.out["alerts_open"] if a["machine_id"] == m.machine_id]
            worst = max((a["severity"] for a in alerts), key=lambda s: rank[s], default=None)
            h = self.health_rows.get(m.machine_id)
            out.append(clean({
                "machine_id": m.machine_id, "site_id": m.site_id, "machine_type": m.machine_type, "model": m.model,
                "status": m.status, "total_engine_hours": m.total_engine_hours, "hours_since_service": m.hours_since_service,
                "service_interval_hours": m.service_interval_hours,
                "live": {
                    "ts": L.ts if L is not None else None, "on_shift": bool(on_shift),
                    "operator_id": L.operator_id if on_shift else None,
                    "operator_name": self.op_name.get(L.operator_id) if on_shift else None,
                    "shift_id": L.shift_id if on_shift else None,
                    "gps_lat": L.gps_lat if L is not None else None, "gps_lon": L.gps_lon if L is not None else None,
                    "fuel_level_pct": L.fuel_level_pct if L is not None else None,
                    "ground_speed_kmh": L.ground_speed_kmh if L is not None else None,
                    "is_idle": bool(L.is_idle) if L is not None else None,
                },
                "health_overall": h["overall"] if h else None,
                "open_alerts": len(alerts), "worst_severity": worst,
            }))
        self.out["fleet"] = {"now": iso(NOW), "machines": out, "geofences": self.geofences()}
        self.meta_notes.append("geofences: none loaded yet; four zones placed inside S1's GPS extent")

    def geofences(self) -> list[dict[str, Any]]:
        g = self.tel_hist[self.tel_hist.machine_id.isin(self.site_machines)]
        la0, la1 = g.gps_lat.quantile([0.02, 0.98])
        lo0, lo1 = g.gps_lon.quantile([0.02, 0.98])
        dla, dlo = la1 - la0, lo1 - lo0

        def rect(a: float, b: float, c: float, d: float) -> dict[str, Any]:
            p = [[lo0 + c * dlo, la0 + a * dla], [lo0 + d * dlo, la0 + a * dla], [lo0 + d * dlo, la0 + b * dla], [lo0 + c * dlo, la0 + b * dla]]
            return {"type": "Polygon", "coordinates": [[[round(x, 6), round(y, 6)] for x, y in [*p, p[0]]]]}

        zones = [
            ("Power line corridor", "power_line", rect(0.86, 0.96, -0.05, 1.05), None),
            ("Open trench, chainage 12+400", "trench", rect(0.10, 0.22, 0.62, 0.80), None),
            ("Site office walkway", "pedestrian", rect(0.02, 0.14, 0.02, 0.18), None),
            ("Haul road", "speed_limited", rect(0.40, 0.50, -0.05, 1.05), 15.0),
        ]
        return [{"id": i, "site_id": DEMO_SITE, "name": n, "zone_type": z, "polygon": poly, "speed_limit_kmh": s,
                 "active": True, "created_by": None, "created_at": "2026-08-10T04:00:00Z"} for i, (n, z, poly, s) in enumerate(zones, 1)]

    # -- clusters ------------------------------------------------------------------------------
    def clusters(self) -> None:
        log("clusters")
        fm = pd.read_csv(CLUSTER_DIR / "fleet_metrics_weekly.csv")
        weeks = sorted(w for w in fm.week_start.unique() if pd.Timestamp(w).tz_localize("UTC") + timedelta(days=7) <= NOW)
        week = weeks[-1]
        rows = fm[(fm.week_start == week) & (fm.site_id == DEMO_SITE)].copy()
        rows.insert(0, "id", range(1, len(rows) + 1))
        rows["verified_by"], rows["verified_at"] = None, None
        pca = json.loads((CLUSTER_DIR / "pca_points.json").read_text())
        s1 = set(fm[fm.site_id == DEMO_SITE].entity_id)
        pts = {k: {"explained_variance": v["explained_variance"], "loadings": v["loadings"],
                   "points": [p for p in v["points"] if p["entity_id"] in s1 and p["week_start"] <= week]} for k, v in pca.items()}
        names = {**self.op_name, **{m: f"{m} · {t.replace('_', ' ')}" for m, t in self.mtype.items()}}
        self.out["clusters"] = clean({"week_start": week, "metrics": records(rows), "pca": pts, "names": {k: v for k, v in names.items() if k in s1}})

    # -- training ------------------------------------------------------------------------------
    def training(self) -> None:
        log("training")
        sc = json.loads((KB / "scenarios.json").read_text(encoding="utf-8"))
        mods = self.modules.copy()
        mods["machine_types"] = mods.machine_types.map(pg_array)
        mods["languages"] = mods.languages.map(pg_array)
        mods["scenario"] = mods.scenario.map(parse_json)
        mods = records(mods)
        sim = {"module_id": sc["module_id"], "title": sc["title"], "topic": "safety", "format": "scenario", "duration_min": 20,
               "difficulty": 2, "content_path": None, "target_metric": "cluster_label",
               "machine_types": ["excavator", "wheel_loader", "dozer", "articulated_truck"], "languages": ["en"],
               "scenario": {"steps": sc["steps"]}}
        mods = [m for m in mods if m["module_id"] != sc["module_id"]] + [sim]
        recs = self.recommendations()
        rec = self.records[self.records.operator_id == DEMO_OPERATOR].sort_values("started_at", ascending=False)
        self.out["training"] = {"modules": mods, "records": records(rec), "recommendations": recs}

    def recommendations(self) -> list[dict[str, Any]]:
        """models.md §10 rules on the operator's last 7 days (max 2)."""
        since = NOW - timedelta(days=7)
        g = self.tel_hist[(self.tel_hist.operator_id == DEMO_OPERATOR) & (self.tel_hist.ts > since) & (self.tel_hist.ts <= NOW)]
        on = g[g.engine_rpm > 0]
        idle = float(on.is_idle.mean() * 100) if len(on) else 0.0
        ev = self.safety[(self.safety.operator_id == DEMO_OPERATOR) & (self.safety.ts > since) & (self.safety.ts <= NOW)].event_type.value_counts()
        fm = pd.read_csv(CLUSTER_DIR / "fleet_metrics_weekly.csv")
        last = fm[(fm.entity_id == DEMO_OPERATOR)].sort_values("week_start")
        last = last[last.week_start.map(lambda w: pd.Timestamp(w).tz_localize("UTC") + timedelta(days=7) <= NOW)].tail(1)
        cands: list[tuple[float, str, str, str, float]] = []  # (strength, module, reason, metric, value)
        if idle > 25:
            cands.append((idle / 25, "TM-IDLE-01", f"You idled {idle:.0f}% of engine time last week. This 8-minute module shows where the minutes go.", "idle_pct", idle))
        if ev.get("harsh_maneuver", 0) >= 3:
            n = int(ev["harsh_maneuver"])
            cands.append((n / 3, "TM-SMTH-01", f"{n} harsh movements last week. Smoother controls save fuel and wear.", "harsh_maneuver", n))
        prox = int(ev.get("proximity_breach", 0) + ev.get("blindspot_intrusion", 0))
        if prox >= 2:
            cands.append((prox / 2, "TM-SAFE-01", f"People came close to your machine {prox} times last week. A 15-minute refresher on blind spots.", "proximity_breach", prox))
        if ev.get("seatbelt_unfastened", 0) >= 1:
            n = int(ev["seatbelt_unfastened"])
            cands.append((1.5 * n, "TM-SAFE-02", f"The seatbelt was unfastened while working {n} time{'s' if n > 1 else ''} last week.", "seatbelt_unfastened", n))
        if ev.get("tip_risk", 0) >= 1:
            n = int(ev["tip_risk"])
            cands.append((1.5 * n, "TM-SAFE-03", f"The machine tilted past the warning angle {n} time{'s' if n > 1 else ''} last week.", "tip_risk", n))
        if ev.get("fatigue_high", 0) >= 3:
            n = int(ev["fatigue_high"])
            cands.append((n / 3, "TM-FAT-01", f"Fatigue reached high {n} times on your shifts last week. 8 minutes on staying sharp at night.", "fatigue_high", n))
        if len(last) and last.cluster_label.iloc[0] == "needs safety coaching":
            cands.append((2.0, "TM-SIM-01", "Your safety pattern last week matches operators who benefit from practice scenarios.", "cluster_label", 0))
        if len(last) and float(last.time_ratio.iloc[0]) > 1.2:
            v = float(last.time_ratio.iloc[0])
            cands.append((v, "TM-EXC-01", f"Tasks took {v:.1f}× the typical time last week.", "time_ratio", v))
        cands.sort(key=lambda c: -c[0])
        out = []
        for i, (_, mod, reason, metric, value) in enumerate(cands[:2], 1):
            out.append({"id": i, "operator_id": DEMO_OPERATOR, "module_id": mod, "reason": reason, "trigger_metric": metric,
                        "trigger_value": round(float(value), 2), "status": "pending", "created_at": iso(NOW - timedelta(hours=2))})
        log(f"recommendations: {[r['module_id'] for r in out]} (idle {idle:.0f}%, events {ev.to_dict()})")
        return out

    # -- chat ----------------------------------------------------------------------------------
    def chat(self) -> None:
        evalset = json.loads((KB / "rag_eval.json").read_text(encoding="utf-8"))["questions"]
        files = ["fault_codes.md", "troubleshooting_faq.md", "safety_rules.md", "operating_tips.md", "training_modules.md"]
        sections: dict[tuple[str, str], tuple[int, str, str]] = {}
        for f in files:
            text = (KB / f).read_text(encoding="utf-8")
            title = re.search(r"^# (.+)$", text, re.M).group(1)  # type: ignore[union-attr]
            for i, part in enumerate(re.split(r"^## ", text, flags=re.M)[1:]):
                head, _, body = part.partition("\n")
                sections[(f, head.strip())] = (i, title, body.strip())
        pick = ["Q01", "Q04", "Q17", "Q18", "Q08", "Q21", "Q25"]
        out = []
        for q in (x for x in evalset if x["id"] in pick):
            if q["source"] is None:
                out.append({"question": q["question"], "answer": "That isn't in the manuals I have. Ask your supervisor or maintenance before you go ahead.", "sources": []})
                continue
            idx, title, body = sections[(q["source"], q["section"])]
            paras = [re.sub(r"\*\*|`|^[-*] |^\d+\. ", "", p, flags=re.M).strip() for p in body.split("\n\n") if p.strip() and not p.lstrip().startswith("|")]
            answer = " ".join(" ".join(p.split("\n")) for p in paras[:2])
            out.append({"question": q["question"], "answer": answer[:700], "sources": [{"document_id": files.index(q["source"]) + 1, "title": title, "chunk_index": idx}]})
        self.out["chat"] = {"session_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "demo-chat")), "examples": out}

    # -- plan ----------------------------------------------------------------------------------
    def plan(self) -> None:
        log("plan re-evaluation")
        F = self.task_F.merge(self.task_rows[["task_id", "priority", "sequence_no", "actual_start", "predicted_p50_min", "predicted_p90_min"]], on="task_id")
        F["status"] = F.task_id.map(self.task_rows.set_index("task_id").status)
        running = F[F.status == "in_progress"]
        wx = self.weather_at(NOW)
        fat = self.out["fatigue"][-1]["fatigue_level"]
        # fatigue peaked at high inside the window; the plan is re-evaluated on that trigger
        peak = max(self.out["fatigue"], key=lambda r: r["fatigue_score"])["fatigue_level"]
        h = self.health_rows.get(DEMO_MACHINE, {}).get("overall")
        trig = P.detect_triggers(now=NOW, running_task=running.iloc[0].to_dict() if len(running) else None, rain_mm_h=wx["rain_mm"],
                                 health_overall=h, fatigue_level=peak)
        cond = {k: wx[k] for k in T.WEATHER_COLUMNS} | ({"health_score": h} if h is not None else {})
        res = P.re_evaluate_plan(DEMO_SHIFT, F, NOW, self.shift.end_time, self.shift.start_time, cond, reason=trig or ["manager_edit"])
        for row in [*res.get("schedule", []), res.get("break") or {}]:
            for k in ("start", "end"):
                if row.get(k):
                    row[k] = iso(pd.Timestamp(row[k]).round("s"))
        self.out["plan"] = clean({**res, "now": NOW, "fatigue_level_now": fat})

    # -- write ---------------------------------------------------------------------------------
    def write(self) -> None:
        OUT.mkdir(parents=True, exist_ok=True)
        meta = {"generated_at": iso(datetime.now(UTC)), "source": self.src.name, "now": iso(NOW),
                "demo": {"operator_id": DEMO_OPERATOR, "machine_id": DEMO_MACHINE, "shift_id": DEMO_SHIFT, "site_id": DEMO_SITE},
                "notes": self.meta_notes}
        for name, data in {**self.out, "_meta": meta}.items():
            text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
            for bad in FORBIDDEN:
                if f'"{bad}"' in text:
                    raise SystemExit(f"{name}.json contains {bad}")
            (OUT / f"{name}.json").write_text(text + "\n", encoding="utf-8")
            log(f"  {name}.json  {len(text) / 1024:.0f} KB")


async def main_async(src: Any) -> None:
    b = Build(src)
    b._thr = load_thresholds()
    b.world()
    b.tasks_now()
    b.maintenance()
    await b.replay()
    b.alerts()
    b.health()
    b.stream()
    b.machine_live()
    b.handover()
    b.machine_logs()
    b.safety_data()
    b.fleet()
    b.clusters()
    b.training()
    b.chat()
    b.plan()
    b.write()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--source", choices=["supabase", "files"], default="supabase")
    ap.add_argument("--env", type=Path, default=REPO / "data" / ".env", help="env file with DATABASE_URL")
    ap.add_argument("--data-dir", type=Path, default=DATA, help="generator output for --source files")
    a = ap.parse_args()
    src = PgSource(a.env) if a.source == "supabase" else FileSource(a.data_dir)
    t0 = time.time()
    asyncio.run(main_async(src))
    log(f"done in {time.time() - t0:.0f} s -> {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
