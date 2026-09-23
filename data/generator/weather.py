"""Stage 2: hourly weather per site (synthetic_data.md, Weather)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from common import ist_to_utc, rng_for, to_frame

P_DRY_TO_RAIN = 0.04
P_RAIN_TO_DRY = 0.25


def _base_temp(hour: np.ndarray) -> np.ndarray:
    """Min 24 °C at 05:00, max 36 °C at 14:00 local; cosine rise (9 h) and fall (15 h)."""
    h = np.where(hour < 5, hour + 24, hour).astype(float)
    rising = h <= 14
    return np.where(
        rising,
        30 - 6 * np.cos(np.pi * (h - 5) / 9),
        30 + 6 * np.cos(np.pi * (h - 14) / 15),
    )


def build_weather(cfg: dict[str, Any], sites: pd.DataFrame) -> pd.DataFrame:
    rng = rng_for(cfg["seed"], "weather")
    # One extra day so night shifts that end at 02:00 still have weather.
    n_hours = (cfg["days"] + 1) * 24
    start = ist_to_utc(cfg["start_date"], "00:00")
    ts = pd.date_range(start, periods=n_hours, freq="h")
    local_hour = np.arange(n_hours) % 24

    rows = []
    for site in sites.itertuples():
        temp = _base_temp(local_hour) + rng.normal(0, 1.5, n_hours)
        raining = np.zeros(n_hours, dtype=bool)
        for i in range(1, n_hours):
            p = 1 - P_RAIN_TO_DRY if raining[i - 1] else P_DRY_TO_RAIN
            raining[i] = rng.random() < p
        rain = np.where(raining, rng.gamma(2.0, 2.0, n_hours), 0.0)
        wind = rng.gamma(2.0, 4.0, n_hours)
        humidity = np.clip(
            70 - 1.5 * (temp - 30) + 20 * raining + rng.normal(0, 5, n_hours), 30, 100
        )
        visibility = np.where(
            rain > 5,
            rng.uniform(1_500, 4_000, n_hours),
            np.where(
                raining,
                rng.uniform(5_000, 7_000, n_hours),
                np.clip(8_000 + rng.normal(0, 200, n_hours), 7_000, 8_000),
            ),
        )
        dust = rng.uniform(0.1, 0.3, n_hours)
        if site.site_type == "quarry":
            windy_dry = (~raining) & (wind > 15)
            dust = np.where(windy_dry, rng.uniform(0.4, 0.7, n_hours), dust)
        for i in range(n_hours):
            rows.append(
                {
                    "site_id": site.site_id,
                    "ts": ts[i],
                    "temp_c": temp[i],
                    "humidity_pct": humidity[i],
                    "rain_mm": rain[i],
                    "wind_kmh": wind[i],
                    "visibility_m": visibility[i],
                    "dust_index": dust[i],
                }
            )
    df = to_frame("weather", rows)
    df["id"] = np.arange(1, len(df) + 1)
    return df


def weather_at(weather: pd.DataFrame, site_id: str, ts: pd.Timestamp) -> pd.Series:
    """Weather row for the hour containing ts."""
    w = weather[weather.site_id == site_id]
    idx = w.ts.searchsorted(ts, side="right") - 1
    return w.iloc[max(idx, 0)]
