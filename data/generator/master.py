"""Stage 1: sites, machines, operators (with hidden personality), training_modules.

Master data is always built for the full config and then filtered for --sample, so
M01 or OP03 has the same attributes in the sample and in the full dataset.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from common import rng_for, to_frame

TYPE_ORDER = ["excavator", "wheel_loader", "dozer", "articulated_truck"]
MODEL_BY_TYPE = {
    "excavator": "Cat 320",
    "wheel_loader": "Cat 950",
    "dozer": "Cat D6",
    "articulated_truck": "Cat 745",
}

# Share of operators per hidden personality (synthetic_data.md).
PERSONALITIES = {
    "efficient": 0.25,
    "average": 0.40,
    "idler": 0.15,
    "aggressive": 0.10,
    "novice": 0.10,
}

# OP03 is the demo persona (docs/supabase.md, docs/demo_script.md).
FIXED_NAMES = {"OP03": "Ravi Kumar"}
FIRST_NAMES = [
    "Arjun",
    "Suresh",
    "Karthik",
    "Vijay",
    "Manoj",
    "Senthil",
    "Rajesh",
    "Prakash",
    "Anand",
    "Ganesh",
    "Murugan",
    "Dinesh",
    "Ramesh",
    "Sathish",
    "Lakshmi",
    "Deepa",
    "Imran",
    "Joseph",
    "Harish",
    "Balaji",
    "Naveen",
    "Kavitha",
    "Mohan",
    "Selvam",
]
LAST_NAMES = [
    "Kumar",
    "Raman",
    "Subramanian",
    "Iyer",
    "Pillai",
    "Reddy",
    "Nair",
    "Singh",
    "Yadav",
    "Krishnan",
    "Shankar",
    "Ali",
    "Thomas",
    "Babu",
    "Rao",
    "Sharma",
]


def build_sites(cfg: dict[str, Any]) -> pd.DataFrame:
    rows = [{**s, "timezone": "Asia/Kolkata"} for s in cfg["sites"]]
    return to_frame("sites", rows)


def build_machines(cfg: dict[str, Any]) -> pd.DataFrame:
    rng = rng_for(cfg["seed"], "machines")
    rows = []
    n = 0
    for site in cfg["sites"]:
        for mtype in TYPE_ORDER:
            for _ in range(cfg["machines"][mtype]):
                n += 1
                model = MODEL_BY_TYPE[mtype]
                rows.append(
                    {
                        "machine_id": f"M{n:02d}",
                        "site_id": site["site_id"],
                        "machine_type": mtype,
                        "model": model,
                        "serial_no": f"{model.split()[1]}-{rng.integers(10_000, 99_999)}{n:02d}",
                        "year": int(rng.integers(2016, 2025)),
                        "total_engine_hours": round(float(rng.uniform(2_000, 12_000)), 1),
                        "hours_since_service": round(float(rng.uniform(0, 450)), 1),
                        "service_interval_hours": 500.0,
                        "status": "active",
                        "health_score": 1.0,  # recomputed from service state at the end of the run
                    }
                )
    return to_frame("machines", rows)


def _personality_list(n: int, rng: np.random.Generator) -> list[str]:
    """Exact shares (largest remainder) so every personality is present, then shuffled."""
    raw = {p: share * n for p, share in PERSONALITIES.items()}
    counts = {p: int(v) for p, v in raw.items()}
    for p in sorted(raw, key=lambda p: raw[p] - counts[p], reverse=True)[
        : n - sum(counts.values())
    ]:
        counts[p] += 1
    out = [p for p, c in counts.items() for _ in range(c)]
    rng.shuffle(out)
    return out


def build_operators(cfg: dict[str, Any]) -> pd.DataFrame:
    rng = rng_for(cfg["seed"], "operators")
    n = cfg["operators"]
    site_ids = [s["site_id"] for s in cfg["sites"]]
    personalities = _personality_list(n, rng)
    used: set[str] = set(FIXED_NAMES.values())
    rows = []
    for i in range(n):
        op_id = f"OP{i + 1:02d}"
        personality = personalities[i]
        # Novices are new to the job; everyone else 1-20 years.
        exp = rng.uniform(1, 3) if personality == "novice" else rng.uniform(1, 20)
        exp = round(float(exp) * 2) / 2
        skill = float(np.clip(0.3 + 0.035 * exp + rng.normal(0, 0.08), 0.1, 0.98))
        name = FIXED_NAMES.get(op_id)
        if name is None:
            while name is None or name in used:
                name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            used.add(name)
        languages = ["en"] + [lang for lang, p in (("hi", 0.4), ("ta", 0.6)) if rng.random() < p]
        rows.append(
            {
                "operator_id": op_id,
                "site_id": site_ids[i * len(site_ids) // n],  # even split, OP01.. at S1
                "full_name": name,
                "experience_years": exp,
                "certification_level": 1 if exp < 3 else 2 if exp < 8 else 3,
                "languages": languages,
                "preferred_shift": "night" if rng.random() < 0.3 else "day",
                "skill_score": round(skill, 3),
                "personality": personality,
            }
        )
    return to_frame("operators", rows)


def build_training_modules() -> pd.DataFrame:
    """Fixed seed list, not random."""

    def m(
        module_id: str,
        title: str,
        topic: str,
        fmt: str,
        dur: int,
        diff: int,
        target: str,
        types: list[str] = TYPE_ORDER,
        scenario: dict[str, Any] | None = None,
        ext: str = "mp4",
    ) -> dict[str, Any]:
        return {
            "module_id": module_id,
            "title": title,
            "topic": topic,
            "format": fmt,
            "duration_min": dur,
            "difficulty": diff,
            "content_path": None if fmt == "instructor_session" else f"{module_id}/content.{ext}",
            "target_metric": target,
            "machine_types": types,
            "languages": ["en", "hi", "ta"],
            "scenario": scenario,
        }

    slope_scenario = {
        "steps": [
            {
                "prompt": "You are loading on a 12° side slope and the machine starts to lean.",
                "choices": [
                    "Swing the load uphill",
                    "Lower the bucket to the ground",
                    "Keep working",
                ],
                "answer": 1,
            },
            {
                "prompt": "Roll angle warning is still on after lowering the bucket.",
                "choices": ["Track downhill quickly", "Stop and call the supervisor", "Ignore it"],
                "answer": 1,
            },
        ]
    }
    rows = [
        m(
            "TM-FUEL-01",
            "Fuel-efficient operation",
            "fuel",
            "video",
            12,
            1,
            "fuel_per_productive_hour",
        ),
        m("TM-IDLE-01", "Cutting idle time", "fuel", "document", 8, 1, "idle_pct", ext="pdf"),
        m("TM-SAFE-01", "Blind spots and proximity", "safety", "video", 15, 1, "proximity_breach"),
        m(
            "TM-SAFE-02",
            "Seatbelt and ROPS",
            "safety",
            "quiz",
            5,
            1,
            "seatbelt_unfastened",
            ext="json",
        ),
        m(
            "TM-SAFE-03",
            "Working on slopes",
            "safety",
            "scenario",
            10,
            2,
            "tip_risk",
            scenario=slope_scenario,
            ext="json",
        ),
        m("TM-SMTH-01", "Smooth controls", "technique", "video", 12, 2, "harsh_maneuver"),
        m(
            "TM-HYD-01",
            "Hydraulic system care",
            "maintenance",
            "document",
            10,
            2,
            "anomaly_count",
            ext="pdf",
        ),
        m(
            "TM-FAT-01",
            "Managing fatigue on night shifts",
            "wellbeing",
            "video",
            8,
            1,
            "fatigue_high",
        ),
        m(
            "TM-EXC-01",
            "Excavator trenching technique",
            "technique",
            "instructor_session",
            60,
            3,
            "time_ratio",
            types=["excavator"],
        ),
        m(
            "TM-TRK-01",
            "Haul road discipline",
            "safety",
            "quiz",
            6,
            2,
            "overspeed",
            types=["articulated_truck"],
            ext="json",
        ),
    ]
    return to_frame("training_modules", rows)


def build_master(cfg: dict[str, Any]) -> dict[str, pd.DataFrame]:
    sites, machines, operators = build_sites(cfg), build_machines(cfg), build_operators(cfg)
    if cfg["machine_ids"]:
        machines = machines[machines.machine_id.isin(cfg["machine_ids"])].reset_index(drop=True)
        keep = set(machines.site_id)
        sites = sites[sites.site_id.isin(keep)].reset_index(drop=True)
        operators = operators[operators.site_id.isin(keep)].reset_index(drop=True)
    return {
        "sites": sites,
        "machines": machines,
        "operators": operators,
        "training_modules": build_training_modules(),
    }
