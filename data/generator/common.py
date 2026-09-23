"""Shared helpers: config, seeded RNGs, IST/UTC time, and schema-checked CSV output.

Column names, order, enums and numeric scales are parsed from
supabase/migrations/001_init.sql, the source of truth for the schema.
"""

from __future__ import annotations

import json
import re
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "supabase" / "migrations" / "001_init.sql"

# India has no DST, so a fixed offset is exact and needs no tz database on Windows.
IST = timezone(timedelta(hours=5, minutes=30), "IST")

# Columns filled by Postgres defaults at load time; never written by the generator.
SKIP_COLUMNS = {"created_at", "updated_at"}


# ---------------------------------------------------------------------------
# Config and randomness
# ---------------------------------------------------------------------------
def load_config(path: Path, sample: bool) -> dict[str, Any]:
    cfg: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["is_sample"] = sample
    cfg.setdefault("machine_ids", None)
    cfg.setdefault("anomalies", True)
    cfg.setdefault("failures", True)
    cfg.setdefault("force_night_shift", False)
    if sample:
        cfg.update(cfg["sample"])
    cfg["output_dir"] = REPO_ROOT / cfg["output_dir"]
    return cfg


def rng_for(seed: int, stage: str) -> np.random.Generator:
    """Independent, reproducible stream per stage: changing one stage never shifts another."""
    return np.random.default_rng([seed, zlib.crc32(stage.encode())])


def uuid_from(rng: np.random.Generator) -> str:
    """Reproducible uuid4 string (for client_id columns)."""
    b = bytearray(rng.bytes(16))
    b[6] = (b[6] & 0x0F) | 0x40
    b[8] = (b[8] & 0x3F) | 0x80
    h = b.hex()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------
def ist_to_utc(d: date, hhmm: str) -> pd.Timestamp:
    """Local site time (IST) on date d -> UTC timestamp."""
    h, m = (int(x) for x in hhmm.split(":"))
    local = datetime(d.year, d.month, d.day, h, m, tzinfo=IST)
    return pd.Timestamp(local).tz_convert("UTC")


def ist_hhmm(ts: pd.Timestamp) -> str:
    return ts.tz_convert(IST).strftime("%H:%M")


# ---------------------------------------------------------------------------
# Schema, parsed from the migration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str
    base_type: str
    enum: str | None
    is_array: bool
    not_null: bool
    scale: int | None


def _parse_schema(sql: str) -> tuple[dict[str, list[Column]], dict[str, list[str]]]:
    enums = {
        m[1]: re.findall(r"'([^']+)'", m[2])
        for m in re.finditer(r"create type (\w+)\s+as enum \((.*?)\);", sql, re.S)
    }
    tables: dict[str, list[Column]] = {}
    for m in re.finditer(r"create table (\w+) \((.*?)\n\);", sql, re.S):
        cols = []
        for raw in m[2].splitlines():
            line = raw.split("--")[0].strip().rstrip(",")
            if not line or line.split()[0] in {"unique", "primary", "constraint", "check"}:
                continue
            name, sql_type = line.split()[:2]
            base = sql_type.removesuffix("[]")
            scale = re.match(r"numeric\(\d+,(\d+)\)", sql_type)
            cols.append(
                Column(
                    name=name,
                    sql_type=sql_type,
                    base_type=re.sub(r"\(.*", "", base),
                    enum=base if base in enums else None,
                    is_array=sql_type.endswith("[]"),
                    not_null="not null" in line or "primary key" in line,
                    scale=int(scale[1]) if scale else None,
                )
            )
        tables[m[1]] = cols
    return tables, enums


SCHEMA, ENUMS = _parse_schema(MIGRATION.read_text(encoding="utf-8"))


def columns(table: str) -> list[str]:
    return [c.name for c in SCHEMA[table] if c.name not in SKIP_COLUMNS]


def to_frame(table: str, rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Rows -> DataFrame with exactly the schema columns. Unknown keys are an error."""
    cols = columns(table)
    unknown = {k for r in rows for k in r} - set(cols)
    if unknown:
        raise KeyError(f"{table}: columns not in migration: {sorted(unknown)}")
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------
def _pg_array(values: Any) -> Any:
    if values is None or (isinstance(values, float) and np.isnan(values)):
        return None

    def quote(v: Any) -> str:
        s = str(v)
        return (
            '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
            if re.search(r'[\s,{}"\\]', s)
            else s
        )

    return "{" + ",".join(quote(v) for v in values) + "}"


def _format(s: pd.Series, col: Column) -> pd.Series:
    if col.is_array:
        return s.map(_pg_array)
    t = col.base_type
    if t == "timestamptz":
        return pd.to_datetime(s, utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if t == "date":
        return s.map(lambda v: None if v is None or pd.isna(v) else str(v))
    if t == "numeric" and col.scale is not None:
        return pd.to_numeric(s).round(col.scale)
    if t == "double":  # double precision
        return pd.to_numeric(s).round(6)
    if t in {"smallint", "integer", "bigint"}:
        return pd.to_numeric(s).astype("Int64")
    if t == "boolean":
        return s.map(lambda v: None if v is None or pd.isna(v) else ("true" if v else "false"))
    if t == "jsonb":
        return s.map(
            lambda v: (
                None
                if v is None or (np.isscalar(v) and pd.isna(v))
                else json.dumps(v, separators=(",", ":"), ensure_ascii=False)
            )
        )
    return s


def validate(table: str, df: pd.DataFrame) -> None:
    expected = columns(table)
    if list(df.columns) != expected:
        raise ValueError(f"{table}: columns {list(df.columns)} != migration {expected}")
    for col in SCHEMA[table]:
        if col.name in SKIP_COLUMNS:
            continue
        s = df[col.name]
        # COPY with an explicit column list inserts NULL, not the default: every
        # NOT NULL column must be filled, even ones that have a default.
        if col.not_null and s.map(lambda v: v is None or (np.isscalar(v) and pd.isna(v))).any():
            raise ValueError(f"{table}.{col.name}: NOT NULL column has nulls")
        if col.enum:
            allowed = set(ENUMS[col.enum])
            vals = {v for arr in s.dropna() for v in arr} if col.is_array else set(s.dropna())
            bad = vals - allowed
            if bad:
                raise ValueError(f"{table}.{col.name}: {sorted(bad)} not in enum {col.enum}")


def write_csv(table: str, df: pd.DataFrame, out_dir: Path) -> int:
    validate(table, df)
    out = pd.DataFrame(
        {c.name: _format(df[c.name], c) for c in SCHEMA[table] if c.name not in SKIP_COLUMNS}
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_dir / f"{table}.csv", index=False, na_rep="")
    return len(out)


def _parquet_column(s: pd.Series, col: Column) -> pd.Series:
    """Native parquet types: tz-aware UTC timestamps, numerics rounded to the SQL scale."""
    t = col.base_type
    if t == "timestamptz":
        return pd.to_datetime(s, utc=True)
    if t == "numeric" and col.scale is not None:
        return pd.to_numeric(s).astype(float).round(col.scale)
    if t == "double":
        return pd.to_numeric(s).astype(float)
    if t in {"smallint", "integer", "bigint"}:
        return pd.to_numeric(s).astype("Int64")
    if t == "boolean":
        return s.astype("boolean")
    return s.astype("string")


def write_parquet(table: str, df: pd.DataFrame, out_dir: Path) -> int:
    validate(table, df)
    out = pd.DataFrame(
        {
            c.name: _parquet_column(df[c.name], c)
            for c in SCHEMA[table]
            if c.name not in SKIP_COLUMNS
        }
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_dir / f"{table}.parquet", index=False, compression="zstd")
    return len(out)
