"""PostgreSQL persistence for fofoca-data.

All writes go through this module. Fund-level atomicity rules (per spec):

  * metadata + NAV upserts + SUCCESS sync state commit in ONE transaction;
  * on fund failure the data transaction is rolled back; FAILED state is
    written in a SEPARATE short transaction (so a failed fund does not lose
    prior coverage), without advancing prior first/last dates;
  * full-history non-NULL values upsert (including corrected historical
    values) and may replace a stored non-NULL value;
  * daily-snapshot values may only fill a stored NULL — they never revise a
    stored non-NULL value, never replace a non-NULL NAV with NULL, and never
    delete a row because a response omits it;
  * ``first_data_date``/``last_data_date`` are the min/max of actually stored
    rows for the fund (observed coverage, not contiguous coverage).

The connection string comes from the environment; no credentials are stored
in the repository.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from importlib import resources
from typing import Iterable

import psycopg
from psycopg import Connection

from .eligibility import FundCandidate
from .redact import sanitize_error as _sanitize_error_impl

DATASET_NAV_DAILY = "NAV_DAILY"


def _load_schema_sql() -> str:
    """Load the schema SQL bundled inside the installed package.

    Works both from a source checkout and from a non-editable wheel install;
    the SQL file is declared as ``package-data`` in ``pyproject.toml``.
    """
    resource = resources.files("fofoca_data").joinpath("sql/001_fofoca.sql")
    return resource.read_text(encoding="utf-8")


def apply_schema(conn: Connection) -> None:
    """Create the three fofoca tables if they do not exist (idempotent)."""
    sql = _load_schema_sql()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NavWrite:
    """A per-date write intent, split into historical and snapshot contributions."""

    nav_date: date
    historical_unit_nav: Decimal | None = None
    historical_accumulated_nav: Decimal | None = None
    snapshot_unit_nav: Decimal | None = None
    snapshot_accumulated_nav: Decimal | None = None


@dataclass(frozen=True)
class FundWriteOutcome:
    fund_id: int
    stored_rows: int
    first_data_date: date | None
    last_data_date: date | None


_UPSERT_FUND_SQL = """
INSERT INTO fund (code, name, fund_type, status, established_date)
VALUES (%(code)s, %(name)s, %(fund_type)s, 'ACTIVE', %(established_date)s)
ON CONFLICT (code) DO UPDATE
    SET name = EXCLUDED.name,
        fund_type = EXCLUDED.fund_type,
        updated_at = now()
RETURNING id
"""

# Historical values are authoritative and may revise a stored non-NULL
# value; a stored non-NULL is never replaced by NULL. Snapshot values only
# fill a stored NULL; they never revise a stored non-NULL.
# Cast parameters so psycopg binds NULLs as numeric (not text).
_UPSERT_NAV_SQL = """
INSERT INTO fund_nav_daily (fund_id, nav_date, unit_nav, accumulated_nav)
VALUES (
    %(fund_id)s,
    %(nav_date)s,
    COALESCE(%(hist_unit)s::numeric, %(snap_unit)s::numeric),
    COALESCE(%(hist_acc)s::numeric, %(snap_acc)s::numeric)
)
ON CONFLICT (fund_id, nav_date) DO UPDATE
    SET unit_nav = COALESCE(
            %(hist_unit)s::numeric,
            fund_nav_daily.unit_nav,
            %(snap_unit)s::numeric
        ),
        accumulated_nav = COALESCE(
            %(hist_acc)s::numeric,
            fund_nav_daily.accumulated_nav,
            %(snap_acc)s::numeric
        ),
        updated_at = now()
"""

_SELECT_COVERAGE_SQL = """
SELECT min(nav_date), max(nav_date), count(*)
FROM fund_nav_daily
WHERE fund_id = %(fund_id)s
"""

_UPSERT_SUCCESS_STATE_SQL = """
INSERT INTO fund_sync_state (
    fund_id, dataset, first_data_date, last_data_date,
    last_sync_at, last_sync_status, last_error
) VALUES (
    %(fund_id)s, %(dataset)s, %(first)s, %(last)s,
    %(sync_at)s, 'SUCCESS', NULL
)
ON CONFLICT (fund_id, dataset) DO UPDATE
    SET first_data_date = EXCLUDED.first_data_date,
        last_data_date = EXCLUDED.last_data_date,
        last_sync_at = EXCLUDED.last_sync_at,
        last_sync_status = 'SUCCESS',
        last_error = NULL,
        updated_at = now()
"""

_UPSERT_FAILED_STATE_SQL = """
INSERT INTO fund_sync_state (
    fund_id, dataset, first_data_date, last_data_date,
    last_sync_at, last_sync_status, last_error
) VALUES (
    %(fund_id)s, %(dataset)s, NULL, NULL,
    %(sync_at)s, 'FAILED', %(error)s
)
ON CONFLICT (fund_id, dataset) DO UPDATE
    SET last_sync_at = EXCLUDED.last_sync_at,
        last_sync_status = 'FAILED',
        last_error = EXCLUDED.last_error,
        updated_at = now()
"""


def _sanitize_error(message: str, *, max_len: int = 500) -> str:
    """Backward-compatible shim — delegates to ``redact.sanitize_error``."""
    return _sanitize_error_impl(message, max_len=max_len)


def record_fund_success(
    conn: Connection,
    candidate: FundCandidate,
    writes: Iterable[NavWrite],
    *,
    established_date: date | None = None,
    sync_at: datetime | None = None,
) -> FundWriteOutcome:
    """Commit one fund's metadata + NAV upserts + SUCCESS state atomically."""
    sync_at = sync_at or datetime.now(tz=timezone.utc)
    writes_list = list(writes)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                _UPSERT_FUND_SQL,
                {
                    "code": candidate.code,
                    "name": candidate.name,
                    "fund_type": candidate.fund_type,
                    "established_date": established_date,
                },
            )
            fund_id = int(cur.fetchone()[0])

            for w in writes_list:
                cur.execute(
                    _UPSERT_NAV_SQL,
                    {
                        "fund_id": fund_id,
                        "nav_date": w.nav_date,
                        "hist_unit": w.historical_unit_nav,
                        "hist_acc": w.historical_accumulated_nav,
                        "snap_unit": w.snapshot_unit_nav,
                        "snap_acc": w.snapshot_accumulated_nav,
                    },
                )

            cur.execute(_SELECT_COVERAGE_SQL, {"fund_id": fund_id})
            row = cur.fetchone()
            first_d: date | None = row[0]
            last_d: date | None = row[1]
            stored_rows: int = int(row[2])

            cur.execute(
                _UPSERT_SUCCESS_STATE_SQL,
                {
                    "fund_id": fund_id,
                    "dataset": DATASET_NAV_DAILY,
                    "first": first_d,
                    "last": last_d,
                    "sync_at": sync_at,
                },
            )
    return FundWriteOutcome(
        fund_id=fund_id,
        stored_rows=stored_rows,
        first_data_date=first_d,
        last_data_date=last_d,
    )


def record_fund_failure(
    conn: Connection,
    candidate: FundCandidate,
    error: BaseException | str,
    *,
    sync_at: datetime | None = None,
) -> int:
    """Record a FAILED attempt in a separate short transaction.

    The fund row is upserted (so the FK target exists) but NAV data is not
    touched, and prior first/last dates are preserved by the ON CONFLICT
    clause. Returns the fund id.
    """
    sync_at = sync_at or datetime.now(tz=timezone.utc)
    error_text = _sanitize_error(str(error))
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                _UPSERT_FUND_SQL,
                {
                    "code": candidate.code,
                    "name": candidate.name,
                    "fund_type": candidate.fund_type,
                    "established_date": None,
                },
            )
            fund_id = int(cur.fetchone()[0])
            cur.execute(
                _UPSERT_FAILED_STATE_SQL,
                {
                    "fund_id": fund_id,
                    "dataset": DATASET_NAV_DAILY,
                    "sync_at": sync_at,
                    "error": error_text,
                },
            )
    return fund_id


# ---------------------------------------------------------------------------
# Read-only query path
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredNavRow:
    nav_date: date
    unit_nav: Decimal | None
    accumulated_nav: Decimal | None


_QUERY_SQL = """
SELECT n.nav_date, n.unit_nav, n.accumulated_nav
FROM fund_nav_daily n
JOIN fund f ON f.id = n.fund_id
WHERE f.code = %(code)s
  AND n.nav_date >= %(start)s
  AND n.nav_date <= %(end)s
ORDER BY n.nav_date ASC
"""


def query_nav_range(
    conn: Connection,
    code: str,
    start: date,
    end: date,
) -> list[StoredNavRow]:
    """Read stored rows for ``code`` in the inclusive range; empty if none.

    This function performs no writes and never calls upstream.
    """
    with conn.cursor() as cur:
        cur.execute(_QUERY_SQL, {"code": code, "start": start, "end": end})
        return [
            StoredNavRow(nav_date=r[0], unit_nav=r[1], accumulated_nav=r[2])
            for r in cur.fetchall()
        ]


__all__ = [
    "DATASET_NAV_DAILY",
    "NavWrite",
    "FundWriteOutcome",
    "StoredNavRow",
    "apply_schema",
    "record_fund_success",
    "record_fund_failure",
    "query_nav_range",
]
