"""Synthetic data generator CLI.

    python data/generator/generate.py --config data/generator/config.yaml            # full run
    python data/generator/generate.py --config data/generator/config.yaml --sample   # 1 day

Full run: every table as CSV in data/output/, telemetry as telemetry.parquet, and ground
truth that is not a schema table in data/output/truth/ (anomaly events, failures).
--sample: 1 day, the machines in config `sample.machine_ids`, no anomalies or failures,
all tables as CSV in data/output/sample/.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

from common import load_config, write_csv, write_parquet
from events import build_events, build_training_records
from handover import build_handover
from maintenance import build_maintenance
from master import build_master
from shifts import build_shifts
from tasks import build_tasks
from telemetry import build_telemetry
from weather import build_weather

# Load order (FKs), as in docs/supabase.md §9.
TABLE_ORDER = [
    "sites",
    "operators",
    "machines",
    "shifts",
    "weather",
    "tasks",
    "maintenance_log",
    "telemetry",
    "safety_events",
    "fatigue_log",
    "incidents",
    "training_modules",
    "training_records",
]
PARQUET_TABLES = {"telemetry"}  # full run only; the sample stays all-CSV for the mocks


class Timer:
    def __init__(self) -> None:
        self.t0 = self.last = time.perf_counter()

    def lap(self, stage: str) -> None:
        now = time.perf_counter()
        print(f"  {stage:<18} {now - self.last:6.1f} s")
        self.last = now


def write_truth(out_dir: Path, anomaly_events: pd.DataFrame, failures: pd.DataFrame) -> None:
    truth = out_dir / "truth"
    truth.mkdir(parents=True, exist_ok=True)
    ev = anomaly_events.copy()
    ev["details"] = ev.details.map(lambda d: json.dumps(d, separators=(",", ":")))
    ev.to_csv(truth / "anomaly_events.csv", index=False)
    failures.to_csv(truth / "failures.csv", index=False)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument(
        "--sample", action="store_true", help="1 day, 2 machines, no anomalies/failures"
    )
    args = parser.parse_args()

    cfg = load_config(args.config, sample=args.sample)
    timer = Timer()
    print("Building")

    master = build_master(cfg)
    sites, machines, operators = master["sites"], master["machines"], master["operators"]
    weather = build_weather(cfg, sites)
    shifts = build_shifts(cfg, machines, operators)
    timer.lap("master+weather+shifts")
    maintenance_log, shift_state, machines, shifts, failures = build_maintenance(
        cfg, machines, shifts
    )
    tasks, task_work = build_tasks(cfg, shifts, shift_state, machines, operators, sites, weather)
    timer.lap("maintenance+tasks")
    telemetry, anomaly_events = build_telemetry(
        cfg, shifts, shift_state, task_work, tasks, machines, operators, sites, weather, failures
    )
    timer.lap("telemetry")
    fatigue_log, safety_events, incidents = build_events(
        cfg, shifts, telemetry, operators, anomaly_events, failures
    )
    training_records = build_training_records(cfg, operators, master["training_modules"])
    shifts = build_handover(
        cfg,
        shifts,
        machines,
        tasks,
        task_work,
        telemetry,
        safety_events,
        anomaly_events,
        failures,
    )
    timer.lap("events+handover")

    tables = {
        "sites": sites,
        "operators": operators,
        "machines": machines,
        "shifts": shifts,
        "weather": weather,
        "tasks": tasks,
        "maintenance_log": maintenance_log,
        "telemetry": telemetry,
        "safety_events": safety_events,
        "fatigue_log": fatigue_log,
        "incidents": incidents,
        "training_modules": master["training_modules"],
        "training_records": training_records,
    }
    out_dir = cfg["output_dir"]
    print(f"Writing to {out_dir}")
    for name in TABLE_ORDER:
        parquet = name in PARQUET_TABLES and not cfg["is_sample"]
        write = write_parquet if parquet else write_csv
        n = write(name, tables[name], out_dir)
        print(f"  {name:<18} {n:>9,} rows  {'parquet' if parquet else 'csv'}")
    if not cfg["is_sample"]:
        write_truth(out_dir, anomaly_events, failures)
        print(
            f"  truth/              {len(anomaly_events):>7,} anomaly events, "
            f"{len(failures)} failures"
        )
    timer.lap("write")
    print(f"Done in {time.perf_counter() - timer.t0:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
