"""operators table, including the hidden `personality`.

`personality` is only used by the task-duration formula (and later telemetry behaviour) and by
validation. Nothing else in the generator may read it.
"""

from typing import Any

import numpy as np
import pandas as pd

PERSONALITY_SHARES = {
    "efficient": 0.25,
    "average": 0.40,
    "idler": 0.15,
    "aggressive": 0.10,
    "novice": 0.10,
}
LANGUAGE_P = {"en": 0.6, "hi": 0.35, "ta": 0.85}  # Vellore: Tamil is most common
NIGHT_PREFERENCE_P = 0.35
# fixed weekly rest day gives ~6 working days per week; small chance of extra leave per day
LEAVE_P = 0.02

FIRST_NAMES = [
    "Arun",
    "Murugan",
    "Senthil",
    "Karthik",
    "Ravi",
    "Selvam",
    "Priya",
    "Lakshmi",
    "Suresh",
    "Vignesh",
    "Rajesh",
    "Anand",
    "Deepa",
    "Manoj",
    "Prakash",
    "Ganesh",
    "Kavitha",
    "Balaji",
    "Dinesh",
    "Saravanan",
    "Vijay",
    "Ramesh",
    "Sanjay",
    "Imran",
    "Joseph",
    "Meena",
]
SURNAMES = [
    "Kumar",
    "Rajan",
    "Pillai",
    "Subramanian",
    "Natarajan",
    "Krishnan",
    "Iyer",
    "Singh",
    "Yadav",
    "Sharma",
    "Raman",
    "Velu",
    "Shanmugam",
    "Khan",
    "Thomas",
    "Reddy",
]


def personality_quotas(n: int) -> list[str]:
    """Exact counts by largest remainder; every personality at least once."""
    raw = {k: v * n for k, v in PERSONALITY_SHARES.items()}
    counts = {k: int(np.floor(v)) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - counts[k], reverse=True)[
        : n - sum(counts.values())
    ]:
        counts[k] += 1
    for k in list(counts):
        if counts[k] == 0:
            big = max(counts, key=counts.get)
            counts[big] -= 1
            counts[k] = 1
    return [k for k, c in counts.items() for _ in range(c)]


def build_operators(cfg: dict[str, Any], rng: np.random.Generator) -> pd.DataFrame:
    n = int(cfg["operators"])
    site_ids = [s["site_id"] for s in cfg["sites"]]
    per_site = n // len(site_ids)
    assert per_site * len(site_ids) == n, "operators must split evenly across sites"

    personalities = rng.permutation(personality_quotas(n))
    first = rng.choice(FIRST_NAMES, size=n, replace=False)
    last = rng.choice(SURNAMES, size=n, replace=True)
    rows = []
    for i in range(n):
        exp = round(float(rng.uniform(1, 20)), 1)
        skill = float(np.clip(0.3 + 0.035 * exp + rng.normal(0, 0.08), 0.1, 0.98))
        cert = 1 if exp < 3 else 2 if exp < 8 else 3
        langs = [lang for lang, p in LANGUAGE_P.items() if rng.random() < p]
        if not langs:
            langs = ["ta"]
        rows.append(
            {
                "operator_id": f"OP{i + 1:02d}",
                "site_id": site_ids[i // per_site],
                "full_name": f"{first[i]} {last[i]}",
                "experience_years": exp,
                "certification_level": cert,
                "languages": langs,
                "preferred_shift": "night" if rng.random() < NIGHT_PREFERENCE_P else "day",
                "skill_score": round(skill, 3),
                "personality": str(personalities[i]),
            }
        )
    df = pd.DataFrame(rows)
    # helper: fixed weekly rest day (0=Mon), spread across the week within each site
    rest = np.empty(n, dtype=int)
    for s in site_ids:
        idx = np.flatnonzero(df["site_id"].to_numpy() == s)
        days = np.concatenate([np.arange(7), rng.integers(0, 7, size=max(0, len(idx) - 7))])
        rest[idx] = rng.permutation(days[: len(idx)])
    df["rest_weekday"] = rest
    return df
