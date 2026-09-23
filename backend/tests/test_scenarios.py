"""Each scripted scenario escalates through the expected stages in order, through the full
replay engine (fake telemetry source, in-memory store, real anomaly model and health score)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from conftest import T0, row

from app.replay.engine import ReplayEngine, ReplayError
from app.replay.writer import MemoryStore

MACHINES = {
    "M01": {"machine_id": "M01", "site_id": "S1", "machine_type": "excavator"},
    "M04": {"machine_id": "M04", "site_id": "S1", "machine_type": "wheel_loader"},
}

# scenario -> (alert_code, [(stage, minutes after the trigger)], severity at the end)
EXPECTED: dict[str, tuple[str, list[tuple[str, float]]]] = {
    "overheating": (
        "COOLANT_CRITICAL",
        [
            ("warn", 7),
            ("derate", 9),
            ("recommend_shutdown", 10),
            ("escalated", 12),
            ("resolved", 22),
        ],
    ),
    "hydraulic_leak": (
        "HYD_PRESSURE_DROP",
        [
            ("warn", 9),
            ("derate", 11),
            ("recommend_shutdown", 14),
            ("escalated", 16),
            ("resolved", 19),
        ],
    ),
    "tip_risk": ("TIP_RISK", [("warn", 2), ("escalated", 6), ("resolved", 9)]),
    "seatbelt": ("SEATBELT", [("warn", 0), ("escalated", 145 / 60), ("resolved", 5)]),
}


IDLE = {
    "is_idle": True,
    "engine_rpm": 900.0,
    "engine_load_pct": 10.0,
    "hydraulic_pressure_bar": 30.0,
    "oil_pressure_kpa": 200.0,
    "fuel_rate_lph": 3.0,
    "ground_speed_kmh": 0.0,
}


class FakeSource:
    name = "parquet"

    def __init__(
        self, machine_ids: list[str], minutes: int = 120, idle_from: int | None = None
    ) -> None:
        """Steady working rows; from minute `idle_from` on the recorded machine idles."""
        self.rows = {
            m: [
                {
                    **row(i, **(IDLE if idle_from is not None and i >= idle_from else {})),
                    "machine_id": m,
                }
                for i in range(-60, minutes)
            ]
            for m in machine_ids
        }

    def fetch(self, machine_id, after, until=None, limit=1000):
        out = [
            r
            for r in self.rows[machine_id]
            if r["ts"] > after and (until is None or r["ts"] <= until)
        ]
        return [dict(r) for r in out[:limit]]


class Pusher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, Any]] = []

    async def send(self, machine_id: str, kind: str, data: Any) -> None:
        self.messages.append((machine_id, kind, data))


async def start_engine(score: bool = False, machines=("M01", "M04"), idle_from: int | None = None):
    pusher, store = Pusher(), MemoryStore()
    eng = ReplayEngine(pusher=pusher, store=store, score=score)
    source = FakeSource(list(machines), idle_from=idle_from)
    await eng.start(list(machines), T0, 10, source, MACHINES, run_loop=False)
    return eng, pusher, store


def ts_min(iso: str, start) -> float:
    from datetime import datetime

    return (datetime.fromisoformat(iso.replace("Z", "+00:00")) - start).total_seconds() / 60


ONLY_CODES = {
    "overheating": {"COOLANT_HIGH", "COOLANT_CRITICAL"},
    "hydraulic_leak": {"HYD_PRESSURE_DROP"},
    "tip_risk": {"TIP_RISK"},
    "seatbelt": {"SEATBELT"},
}


# idle_from=8: the recorded machine goes idle 2.5 min after the trigger while the script still
# works it (seen live: idle hydraulic pressure under a scripted full load looked like a leak)
@pytest.mark.parametrize("idle_from", [None, 8])
@pytest.mark.parametrize("name", list(EXPECTED))
async def test_scenario_stages_in_order(name, idle_from):
    eng, pusher, store = await start_engine(idle_from=idle_from)
    await eng.advance_to(T0 + timedelta(minutes=5, seconds=30))
    sc = eng.trigger(name, "M04")
    await eng.advance_to(sc.end_ts + timedelta(minutes=3))
    await eng.writer.flush()

    code, expected = EXPECTED[name]
    rows = [a for a in store.alerts.values() if a["alert_code"] == code]
    assert len(rows) == 1, f"one {code} alert row expected, got {len(rows)}"
    a = rows[0]
    assert a["machine_id"] == "M04" and a["site_id"] == "S1" and a["operator_id"] == "OP03"
    stages = a["evidence"]["stages"]
    got = [(s["stage"], round(ts_min(s["ts"], sc.start_ts), 2)) for s in stages]
    assert got == [(s, round(m, 2)) for s, m in expected]
    assert a["stage"] == "resolved" and a["resolved_at"] is not None
    assert a["severity"] == "critical"
    # the WebSocket carried each stage with the DB id, in order
    ws = [d for m, k, d in pusher.messages if k == "alert" and d["alert_code"] == code]
    # stage messages in order (a severity rise re-sends the current stage)
    ws_stages = list(dict.fromkeys(d["stage"] for d in ws))
    assert ws_stages == [s for s, _ in expected]
    assert all(d["id"] == a["id"] for d in ws)
    # the script raises only its own alerts
    assert {
        x["alert_code"] for x in store.alerts.values() if x["machine_id"] == "M04"
    } == ONLY_CODES[name]
    # the untouched machine raised nothing
    assert not [x for x in store.alerts.values() if x["machine_id"] == "M01"]
    await eng.stop()


async def test_scenario_rows_replace_recorded_rows_then_hand_back():
    eng, pusher, _ = await start_engine()
    await eng.advance_to(T0 + timedelta(minutes=5, seconds=30))
    sc = eng.trigger("overheating", "M04")
    await eng.advance_to(sc.end_ts + timedelta(minutes=2))
    tele = [d for m, k, d in pusher.messages if k == "telemetry" and m == "M04"]
    ts = [d["ts"] for d in tele]
    assert ts == sorted(ts) and len(ts) == len(set(ts))
    peak = max(d["coolant_temp_c"] for d in tele)
    assert 111 <= peak <= 113
    assert tele[-1]["coolant_temp_c"] == 88.0  # recorded data again
    assert "_period_s" not in tele[-1] and "anomaly_label" not in tele[-1]
    assert eng.status()["scenarios"] == {}
    await eng.stop()


async def test_trigger_errors():
    eng, _, _ = await start_engine(machines=("M01",))
    await eng.advance_to(T0 + timedelta(minutes=1))
    with pytest.raises(ReplayError) as e:
        eng.trigger("overheating", "M04")
    assert e.value.code == "MACHINE_NOT_IN_REPLAY"
    eng.trigger("seatbelt", "M01")
    with pytest.raises(ReplayError) as e:
        eng.trigger("overheating", "M01")
    assert e.value.code == "SCENARIO_RUNNING"
    with pytest.raises(ReplayError) as e:
        eng.trigger("fatigue", "M01")
    assert e.value.status == 501
    await eng.stop()
    with pytest.raises(ReplayError):
        eng.trigger("overheating", "M01")


async def test_health_and_anomaly_every_minute_with_models():
    eng, pusher, store = await start_engine(score=True, machines=("M04",))
    assert eng.anomaly_error is None
    await eng.advance_to(T0 + timedelta(minutes=2, seconds=30))
    sc = eng.trigger("overheating", "M04")
    await eng.advance_to(sc.start_ts + timedelta(minutes=13))
    await eng.writer.flush()
    # one snapshot per data minute: 02:00-02:02 recorded, then scenario rows at 02:03:30 ...
    # 02:15:30 (the first one, 02:02:30, falls in the already scored minute 02:02)
    assert len(store.health) == 3 + 13
    snap = store.health[-1]
    assert set(snap) >= {
        "machine_id",
        "ts",
        "overall_score",
        "cooling_score",
        "anomaly_score",
        "details",
    }
    assert snap["cooling_score"] == pytest.approx(0.3)  # COOLANT_CRITICAL open -> 1 - 0.7
    assert snap["overall_score"] <= 0.3
    health_msgs = [d for m, k, d in pusher.messages if k == "health"]
    assert len(health_msgs) == len(store.health)
    assert set(health_msgs[-1]) == {"overall", "subsystems"}
    h = eng.machine_health("M04")
    assert h["band"] == "red" and h["anomaly_score"] is not None
    state = eng.machine_state("M04")
    assert state["moving"] is True and state["ground_speed_kmh"] == 1.5
    await eng.stop()
