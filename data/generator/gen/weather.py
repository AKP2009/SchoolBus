"""Hourly weather per site (docs/synthetic_data.md, Weather).

Grid: top of every UTC hour from start_date 00:00 UTC through the end of
start_date + days + future_days (inclusive), so the last night shift is covered.
"""

from typing import Any

import numpy as np
import pandas as pd

from gen import LOCAL_TZ

T_MIN, T_MAX, H_MIN, H_MAX = 24.0, 36.0, 5.0, 14.0
P_DRY_TO_RAIN, P_RAIN_TO_DRY = 0.04, 0.25
HEAVY_RAIN_MM = 7.6  # IMD-style "heavy" hourly rate
WINDY_KMH = 15.0


def diurnal_temp(local_hour: np.ndarray) -> np.ndarray:
    """Asymmetric daily sine: min 24 °C at 05:00, max 36 °C at 14:00 local."""
    mid, amp = (T_MAX + T_MIN) / 2, (T_MAX - T_MIN) / 2
    h = np.mod(local_hour - H_MIN, 24.0)  # hours since the minimum
    rise = H_MAX - H_MIN  # 9 h up, 15 h down
    return np.where(
        h <= rise,
        mid - amp * np.cos(np.pi * h / rise),
        mid + amp * np.cos(np.pi * (h - rise) / (24.0 - rise)),
    )


def build_weather(
    cfg: dict[str, Any], sites: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    n_days = int(cfg["days"]) + int(cfg["future_days"]) + 1
    ts = pd.date_range(pd.Timestamp(cfg["start_date"], tz="UTC"), periods=n_days * 24, freq="h")
    local = ts.tz_convert(LOCAL_TZ)
    local_hour = (local.hour + local.minute / 60).to_numpy(dtype=float)
    n = len(ts)

    frames = []
    for site in sites.itertuples():
        temp = diurnal_temp(local_hour) + rng.normal(0, 1.5, n)

        u = rng.random(n)
        raining = np.zeros(n, dtype=bool)
        state = False
        for i in range(n):
            state = (u[i] < P_DRY_TO_RAIN) if not state else (u[i] >= P_RAIN_TO_DRY)
            raining[i] = state
        rain = np.round(np.where(raining, rng.gamma(2.0, 2.0, n), 0.0), 2)

        # wind: afternoon peak + gusty noise, stronger in rain
        wind = 6 + 8 * np.exp(-((local_hour - 15) ** 2) / (2 * 3.0**2)) + rng.gamma(2.0, 2.0, n)
        wind = np.clip(wind + 5 * raining, 0, None)

        vis = np.full(n, 8000.0)
        light = raining & (rain < 2.5)
        moderate = raining & (rain >= 2.5) & (rain < HEAVY_RAIN_MM)
        heavy = raining & (rain >= HEAVY_RAIN_MM)
        vis = np.where(light, rng.uniform(6000, 8000, n), vis)
        vis = np.where(moderate, rng.uniform(4000, 6000, n), vis)
        vis = np.where(heavy, rng.uniform(1500, 4000, n), vis)

        dust = rng.uniform(0.1, 0.3, n)
        if site.site_type == "quarry":
            windy_dry = (~raining) & (wind > WINDY_KMH)
            boost = 0.4 * np.clip((wind - WINDY_KMH) / 10.0, 0, 1)
            dust = np.where(windy_dry, np.minimum(dust + boost, 0.7), dust)
        dust = np.where(raining, 0.1, dust)

        hum = 95 - 2.5 * (temp - T_MIN) + rng.normal(0, 4, n)
        hum = np.where(raining, np.maximum(hum, rng.uniform(90, 100, n)), hum)
        hum = np.clip(hum, 30, 100)

        frames.append(
            pd.DataFrame(
                {
                    "site_id": site.site_id,
                    "ts": ts,
                    "temp_c": np.round(temp, 1),
                    "humidity_pct": np.round(hum, 2),
                    "rain_mm": np.round(rain, 2),
                    "wind_kmh": np.round(wind, 1),
                    "visibility_m": np.round(vis, 1),
                    "dust_index": np.round(dust, 2),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)
