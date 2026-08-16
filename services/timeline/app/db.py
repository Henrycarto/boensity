"""TimescaleDB access for the longitudinal T-score store.

asyncpg directly rather than an ORM. The read pattern here is narrow and
repetitive: pull an ordered scan series for one patient at one site, or run a
continuous aggregate across a cohort. Both are better expressed as SQL than as
generated queries, and the hypertable functions this service depends on
(time_bucket, continuous aggregates) are not portable through an ORM anyway.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import asyncpg

_pool: asyncpg.Pool | None = None


def _dsn() -> str:
    """asyncpg wants a bare postgres URL, not the SQLAlchemy dialect form."""
    raw = os.getenv(
        "TIMESCALE_DSN",
        "postgresql://boensity:boensity_local_dev@localhost:5432/boensity",
    )
    return raw.replace("postgresql+asyncpg://", "postgresql://")


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=_dsn(),
            min_size=int(os.getenv("DB_POOL_MIN", "2")),
            max_size=int(os.getenv("DB_POOL_MAX", "10")),
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool not initialised")
    return _pool


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

_INSERT_SCAN = """
INSERT INTO scan_observations (
    scan_date, patient_id, site, t_score, z_score, bmd_g_cm2,
    manufacturer, scanner_model, source_document_id
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
ON CONFLICT (patient_id, scan_date, site) DO UPDATE SET
    t_score = EXCLUDED.t_score,
    z_score = EXCLUDED.z_score,
    bmd_g_cm2 = EXCLUDED.bmd_g_cm2,
    manufacturer = EXCLUDED.manufacturer,
    scanner_model = EXCLUDED.scanner_model,
    source_document_id = EXCLUDED.source_document_id
"""

_SELECT_SERIES = """
SELECT scan_date, t_score, z_score, bmd_g_cm2, manufacturer, scanner_model, source_document_id
FROM scan_observations
WHERE patient_id = $1 AND site = $2
ORDER BY scan_date ASC
"""

# Patients whose most recent scan is in the osteopenia range and who are
# losing ground fast enough to matter. This is the department head's morning
# screen, so it runs against the continuous aggregate rather than raw rows.
_SELECT_AT_RISK_COHORT = """
SELECT patient_id,
       latest_t_score,
       earliest_t_score,
       scan_count,
       span_years,
       CASE WHEN span_years > 0
            THEN (latest_t_score - earliest_t_score) / span_years
            ELSE NULL END AS t_score_per_year
FROM patient_scan_summary
WHERE site = $1
  AND scan_count >= 2
  AND span_years > 0
  AND latest_t_score > -2.5
  AND (latest_t_score - earliest_t_score) / span_years <= $2
ORDER BY t_score_per_year ASC
LIMIT $3
"""


async def upsert_scan(patient_id: str, scan: dict[str, Any]) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            _INSERT_SCAN,
            scan["scan_date"],
            patient_id,
            scan["site"],
            scan["t_score"],
            scan.get("z_score"),
            scan.get("bmd_g_cm2"),
            scan.get("manufacturer", "unknown"),
            scan.get("scanner_model"),
            scan.get("source_document_id"),
        )


async def fetch_series(patient_id: str, site: str) -> list[dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SELECT_SERIES, patient_id, site)
    return [dict(row) for row in rows]


async def fetch_at_risk_cohort(
    site: str = "femoral_neck",
    max_velocity: float = -0.05,
    limit: int = 100,
) -> list[dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SELECT_AT_RISK_COHORT, site, max_velocity, limit)
    return [dict(row) for row in rows]


async def latest_scan_date(patient_id: str, site: str) -> date | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT max(scan_date) FROM scan_observations WHERE patient_id = $1 AND site = $2",
            patient_id,
            site,
        )
