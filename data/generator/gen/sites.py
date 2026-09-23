"""sites table: straight from config."""

from typing import Any

import pandas as pd

from gen import LOCAL_TZ


def build_sites(cfg: dict[str, Any]) -> pd.DataFrame:
    df = pd.DataFrame(cfg["sites"])
    df["timezone"] = LOCAL_TZ
    return df[["site_id", "name", "site_type", "lat", "lon", "timezone"]]
