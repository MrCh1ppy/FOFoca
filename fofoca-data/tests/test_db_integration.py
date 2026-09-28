"""Integration tests against a disposable PostgreSQL database.

These tests run only when both:
  * ``FOFOCA_INTEGRATION_DB=true``
  * ``FOFOCA_TEST_DATABASE_URL`` points at a *dedicated, disposable* database
    (the tests drop and recreate the fofoca tables).

NEVER point this at Ariadne's database or any production database.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from fofoca_data.db import (
    NavWrite,
    apply_schema,
    query_nav_range,
    read_stored_max_nav_dates,
    record_fund_success,
    stored_max_nav_date,
)
from fofoca_data.eligibility import FundCandidate
from fofoca_data.errors import DatabaseError

pytestmark = pytest.mark.skipif(
    os.environ.get("FOFOCA_INTEGRATION_DB") != "true"
    or not os.environ.get("FOFOCA_TEST_DATABASE_URL"),
    reason="disposable DB integration disabled (set FOFOCA_INTEGRATION_DB=true and FOFOCA_TEST_DATABASE_URL)",
)


@pytest.fixture()
def conn():
    url = os.environ["FOFOCA_TEST_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "DROP TABLE IF EXISTS fund_sync_state, fund_nav_daily, fund CASCADE"
            )
        apply_schema(c)
        yield c


CAND = FundCandidate(code="000001", name="测试基金", fund_type="混合型-灵活")


def _hist(d, u=None, a=None):
    return NavWrite(nav_date=date.fromisoformat(d), historical_unit_nav=u, historical_accumulated_nav=a)


def _snap(d, u=None, a=None):
    return NavWrite(nav_date=date.fromisoformat(d), snapshot_unit_nav=u, snapshot_accumulated_nav=a)


def _mix(d, hu=None, ha=None, su=None, sa=None):
    return NavWrite(
        nav_date=date.fromisoformat(d),
        historical_unit_nav=hu,
        historical_accumulated_nav=ha,
        snapshot_unit_nav=su,
        snapshot_accumulated_nav=sa,
    )


def _read_rows(conn, code="000001"):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT nav_date, unit_nav, accumulated_nav FROM fund_nav_daily "
            "WHERE fund_id=(SELECT id FROM fund WHERE code=%s) ORDER BY nav_date",
            (code,),
        )
        return cur.fetchall()


def _table_exists(conn, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (name,))
        return cur.fetchone()[0] is not None


class TestSuccessWrite:
    def test_first_backfill_writes_metadata_and_nav(self, conn) -> None:
        outcome = record_fund_success(
            conn,
            CAND,
            [
                _hist("2024-01-02", u=Decimal("1.100000")),
                _hist("2024-01-03", u=Decimal("1.200000"), a=Decimal("2.200000")),
            ],
        )
        assert outcome.stored_rows == 2
        assert outcome.first_data_date == date(2024, 1, 2)
        assert outcome.last_data_date == date(2024, 1, 3)

        with conn.cursor() as cur:
            cur.execute("SELECT code, name, fund_type, status FROM fund WHERE id=%s", (outcome.fund_id,))
            row = cur.fetchone()
            assert row == ("000001", "测试基金", "混合型-灵活", "ACTIVE")

    def test_rerun_is_idempotent_and_applies_historical_revision(self, conn) -> None:
        record_fund_success(conn, CAND, [_hist("2024-01-02", u=Decimal("1.100000"))])
        record_fund_success(conn, CAND, [_hist("2024-01-02", u=Decimal("1.150000"))])
        rows = _read_rows(conn)
        assert len(rows) == 1
        assert rows[0][1] == Decimal("1.150000")
        assert rows[0][2] is None

    def test_stored_non_null_never_overwritten_by_null(self, conn) -> None:
        record_fund_success(
            conn, CAND, [_hist("2024-01-02", u=Decimal("1.100000"), a=Decimal("2.200000"))]
        )
        # Later historical response has no accumulated for the same date
        record_fund_success(conn, CAND, [_hist("2024-01-02", u=Decimal("1.100000"))])
        rows = _read_rows(conn)
        assert rows[0][2] == Decimal("2.200000")

    def test_omitted_date_is_not_deleted(self, conn) -> None:
        record_fund_success(
            conn,
            CAND,
            [_hist("2024-01-02", u=Decimal("1.100000")), _hist("2024-01-03", u=Decimal("1.200000"))],
        )
        record_fund_success(conn, CAND, [_hist("2024-01-03", u=Decimal("1.250000"))])
        rows = _read_rows(conn)
        assert [r[0] for r in rows] == [date(2024, 1, 2), date(2024, 1, 3)]

    def test_snapshot_fills_null_but_never_revises_non_null(self, conn) -> None:
        # First run: historical value stored
        record_fund_success(
            conn, CAND, [_hist("2024-01-02", u=Decimal("1.100000"), a=Decimal("2.200000"))]
        )
        # Second run: a different snapshot value arrives for the same date/metric,
        # but no historical value -> stored non-NULL stays
        record_fund_success(
            conn, CAND, [_mix("2024-01-02", su=Decimal("9.999999"), sa=Decimal("9.999999"))]
        )
        rows = _read_rows(conn)
        assert rows[0][1] == Decimal("1.100000")
        assert rows[0][2] == Decimal("2.200000")

    def test_snapshot_fills_null_field(self, conn) -> None:
        # First run: only unit stored
        record_fund_success(conn, CAND, [_hist("2024-01-02", u=Decimal("1.100000"))])
        # Second run: snapshot supplies accumulated for the same date -> fills NULL
        record_fund_success(conn, CAND, [_snap("2024-01-02", a=Decimal("2.500000"))])
        rows = _read_rows(conn)
        assert rows[0][1] == Decimal("1.100000")
        assert rows[0][2] == Decimal("2.500000")

    def test_historical_value_can_revise_snapshot_filled_value(self, conn) -> None:
        # First run: only a snapshot value
        record_fund_success(conn, CAND, [_snap("2024-01-02", u=Decimal("1.500000"))])
        # Second run: the historical value arrives and overrides the snapshot fill
        record_fund_success(conn, CAND, [_hist("2024-01-02", u=Decimal("1.100000"))])
        rows = _read_rows(conn)
        assert rows[0][1] == Decimal("1.100000")

    def test_all_empty_write_is_never_inserted(self, conn) -> None:
        """A NavWrite with no usable value on any metric must not create a row."""
        record_fund_success(conn, CAND, [_hist("2024-01-02", u=Decimal("1.100000"))])
        record_fund_success(
            conn, CAND, [NavWrite(nav_date=date(2024, 1, 3))]  # all metrics None
        )
        rows = _read_rows(conn)
        assert [r[0] for r in rows] == [date(2024, 1, 2)]

    def test_failed_attempt_rolls_back_and_keeps_previous_rows(self, conn) -> None:
        """A write error mid-fund rolls back the whole attempt atomically."""
        record_fund_success(conn, CAND, [_hist("2024-01-02", u=Decimal("1.100000"))])
        good = _hist("2024-01-03", u=Decimal("1.300000"))
        # Force a DB-level failure inside the attempt by exceeding NUMERIC(18,6).
        too_big = NavWrite(
            nav_date=date(2024, 1, 4),
            historical_unit_nav=Decimal("99999999999999.999999") + Decimal("99999999999999"),
        )
        with pytest.raises(Exception):
            record_fund_success(conn, CAND, [good, too_big])
        # Previous committed row survives; the failed attempt left nothing.
        rows = _read_rows(conn)
        assert [r[0] for r in rows] == [date(2024, 1, 2)]


class TestTwoTableSchema:
    def test_schema_creates_exactly_two_tables(self, conn) -> None:
        assert _table_exists(conn, "fund")
        assert _table_exists(conn, "fund_nav_daily")
        assert not _table_exists(conn, "fund_sync_state")

    def test_repeated_init_db_never_recreates_state_table_or_drops_rows(self, conn) -> None:
        record_fund_success(conn, CAND, [_hist("2024-01-02", u=Decimal("1.100000"))])
        for _ in range(3):
            apply_schema(conn)
        assert not _table_exists(conn, "fund_sync_state")
        rows = _read_rows(conn)
        assert len(rows) == 1
        assert rows[0][1] == Decimal("1.100000")

    def test_init_db_on_legacy_three_table_db_keeps_state_table_untouched(self, conn) -> None:
        """init-db must NOT drop the legacy table implicitly (no destructive init)."""
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE fund_sync_state (fund_id BIGINT, dataset VARCHAR(32), "
                "last_sync_status VARCHAR(16), PRIMARY KEY (fund_id, dataset))"
            )
            cur.execute(
                "INSERT INTO fund_sync_state (fund_id, dataset, last_sync_status) "
                "VALUES (1, 'NAV_DAILY', 'SUCCESS')"
            )
        apply_schema(conn)
        assert _table_exists(conn, "fund_sync_state")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fund_sync_state")
            assert cur.fetchone()[0] == 1


class TestStoredMaxNavDate:
    def test_absent_fund_maps_to_none(self, conn) -> None:
        result = read_stored_max_nav_dates(conn, ["123456"])
        assert result == {"123456": None}

    def test_fund_without_nav_maps_to_none(self, conn) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fund (code, name, fund_type) VALUES ('000009', 'X', '混合型')"
            )
        assert stored_max_nav_date(conn, "000009") is None

    def test_fund_with_nav_maps_to_max(self, conn) -> None:
        record_fund_success(
            conn,
            CAND,
            [_hist("2024-01-02", u=Decimal("1.1")), _hist("2026-09-24", u=Decimal("1.2"))],
        )
        assert stored_max_nav_date(conn, "000001") == date(2026, 9, 24)

    def test_batch_lookup_covers_all_codes(self, conn) -> None:
        record_fund_success(conn, CAND, [_hist("2026-09-25", u=Decimal("1.1"))])
        result = read_stored_max_nav_dates(conn, ["000001", "000002", "000001"])
        assert result == {"000001": date(2026, 9, 25), "000002": None}

    def test_lookup_failure_raises_database_error(self, conn) -> None:
        conn.close()
        with pytest.raises(DatabaseError):
            read_stored_max_nav_dates(conn, ["000001"])


class TestQuery:
    def test_inclusive_bounds_and_order(self, conn) -> None:
        record_fund_success(
            conn,
            CAND,
            [
                _hist("2024-01-01", u=Decimal("1.0")),
                _hist("2024-01-05", u=Decimal("1.1"), a=Decimal("2.1")),
                _hist("2024-01-10", a=Decimal("2.5")),
                _hist("2024-01-20", u=Decimal("1.5"), a=Decimal("2.9")),
            ],
        )
        rows = query_nav_range(conn, "000001", date(2024, 1, 5), date(2024, 1, 10))
        assert [r.nav_date for r in rows] == [date(2024, 1, 5), date(2024, 1, 10)]
        assert rows[0].unit_nav == Decimal("1.100000")
        assert rows[0].accumulated_nav == Decimal("2.100000")
        assert rows[1].unit_nav is None
        assert rows[1].accumulated_nav == Decimal("2.500000")

    def test_unknown_code_returns_empty(self, conn) -> None:
        rows = query_nav_range(conn, "999999", date(2024, 1, 1), date(2024, 12, 31))
        assert rows == []

    def test_empty_range_returns_empty(self, conn) -> None:
        record_fund_success(conn, CAND, [_hist("2024-01-02", u=Decimal("1.1"))])
        rows = query_nav_range(conn, "000001", date(2025, 1, 1), date(2025, 12, 31))
        assert rows == []


class TestNoFeeStorage:
    def test_schema_has_no_fee_columns(self, conn) -> None:
        with conn.cursor() as cur:
            for table in ("fund", "fund_nav_daily"):
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name=%s AND table_schema=current_schema()",
                    (table,),
                )
                cols = {r[0] for r in cur.fetchall()}
                assert "fee" not in cols
                assert "手续费" not in cols
