"""Synthetic data generator modules. See docs/synthetic_data.md."""

import zlib

import numpy as np

LOCAL_TZ = "Asia/Kolkata"


def rng_for(seed: int, name: str) -> np.random.Generator:
    """Independent, deterministic Generator per module.

    Each module gets its own stream derived from config.seed, so changing one module's
    draws does not shift the random numbers of the others.
    """
    return np.random.default_rng([seed, zlib.crc32(name.encode())])
