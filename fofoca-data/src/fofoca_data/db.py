"""PostgreSQL persistence for fofoca-data.

All writes go through this module. Fund-level atomicity rules (per spec):

  * one fund's metadata + NAV upserts commit in ONE transaction; there is no
    success/failure state table (removed in v0.2) and no failure-only fund
    row is ever inserted;
  * on fund failure the attempt's transaction is rolled back, previously
    committed funds stay committed, and the failure is reported only through
    live output and the in-memory run report;
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

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from importlib import resources
from typing import Iterable

import psycopg
from psycopg import Connection

from .eligibility import FundCandidate
from .errors import DatabaseError
from .redact import sanitize_error as _sanitize_error_impl

# Kept for backward compatibility with older imports; the state table itself
# no longer exists and this constant is no longer used by the write path.
DATASET_NAV_DAILY = "NAV_DAILY"


def _load_schema_sql() -> str:
    """Load the schema SQL bundled inside the installed package.

    Works both from a source checkout and from a non-editable wheel install;
    the SQL file is declared as ``package-data`` in ``pyproject.toml``.
    """
    resource = resources.files("fofoca_data").joinpath("sql/001_fofoca.sql")
    return resource.read_text(encoding="utf-8")


def apply_schema(conn: Connection) -> None:
    """Create the two fofoca tables if they do not exist (idempotent).

    Never drops or recreates anything. Never creates the removed
    ``fund_sync_state`` table. Safe to run repeatedly against a database that
    already holds fund/NAV data.
    """
    sql = _load_schema_sql()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


# ---------------------------------------------------------------------------
# Stored-coverage reads (target-date support)
# ---------------------------------------------------------------------------


def read_stored_max_nav_dates(
    conn: Connection, codes: Iterable[str]
) -> dict[str, date | None]:
    """Return ``{code: stored MAX(nav_date) or None}`` for each supplied code.

    ``None`` covers both "no fund row" and "fund row but no NAV rows" — both
    mean "no stored coverage" for the target-date skip heuristic.

    This function runs in its own short read-only transaction and leaves the
    connection clean (committed, not idle-in-transaction) so callers can
    perform network I/O immediately afterwards without holding a database
    transaction open.

    Raises ``DatabaseError`` on any lookup failure — a failed lookup must
    never be treated as "no stored coverage".
    """
    code_list = sorted(set(codes))
    if not code_list:
        return {}
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT f.code, MAX(n.nav_date) "
                    "FROM fund f LEFT JOIN fund_nav_daily n ON n.fund_id = f.id "
                    "WHERE f.code = ANY(%(codes)s) "
                    "GROUP BY f.code",
                    {"codes": code_list},
                )
                found = {row[0]: row[1] for row in cur.fetchall()}
    except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 — connection may already be dead
            pass
        raise DatabaseError(
            f"could not read stored NAV coverage: {_sanitize_error_impl(exc)}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise DatabaseError(
            f"could not read stored NAV coverage: {_sanitize_error_impl(exc)}"
        ) from exc
    return {code: found.get(code) for code in code_list}


def stored_max_nav_date(conn: Connection, code: str) -> date | None:
    """Single-code convenience wrapper around :func:`read_stored_max_nav_dates`.

    Uses the same short-transaction semantics and failure policy.
    """
    return read_stored_max_nav_dates(conn, [code])[code]


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

    @property
    def has_any_value(self) -> bool:
        """True when any (historical or usable snapshot) metric is present.

        Dates with no usable historical metric and no valid dated snapshot
        fill must not be inserted; callers filter on this before writing.
        """
        return (
            self.historical_unit_nav is not None
            or self.historical_accumulated_nav is not None
            or self.snapshot_unit_nav is not None
            or self.snapshot_accumulated_nav is not None
        )


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


def _sanitize_error(message: str, *, max_len: int = 500) -> str:
    """Backward-compatible shim — delegates to ``redact.sanitize_error``."""
    return _sanitize_error_impl(message, max_len=max_len)


def record_fund_success(
    conn: Connection,
    candidate: FundCandidate,
    writes: Iterable[NavWrite],
    *,
    established_date: date | None = None,
) -> FundWriteOutcome:
    """Commit one fund's metadata + NAV upserts atomically.

    Writes with no usable value on any metric are skipped here as a final
    safety net (orchestration already filters them), so an all-NULL row can
    never be inserted.
    """
    writes_list = [w for w in writes if w.has_any_value]
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
            fund_row = cur.fetchone()
            if fund_row is None:  # pragma: no cover - defensive
                raise DatabaseError("fund upsert returned no id")
            fund_id = int(fund_row[0])

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
            if row is None:  # pragma: no cover - defensive
                raise DatabaseError("coverage lookup returned no row")
            first_d: date | None = row[0]
            last_d: date | None = row[1]
            stored_rows: int = int(row[2])
    return FundWriteOutcome(
        fund_id=fund_id,
        stored_rows=stored_rows,
        first_data_date=first_d,
        last_data_date=last_d,
    )


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
    "read_stored_max_nav_dates",
    "stored_max_nav_date",
    "query_nav_range",
]
