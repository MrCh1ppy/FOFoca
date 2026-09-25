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
    DATASET_NAV_DAILY,
    NavWrite,
    apply_schema,
    query_nav_range,
    record_fund_failure,
    record_fund_success,
)
from fofoca_data.eligibility import FundCandidate

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


class TestSuccessWrite:
    def test_first_backfill_writes_metadata_nav_and_state(self, conn) -> None:
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

            cur.execute(
                "SELECT dataset, first_data_date, last_data_date, last_sync_status, last_error "
                "FROM fund_sync_state WHERE fund_id=%s",
                (outcome.fund_id,),
            )
            state = cur.fetchone()
            assert state[0] == DATASET_NAV_DAILY
            assert state[1] == date(2024, 1, 2)
            assert state[2] == date(2024, 1, 3)
            assert state[3] == "SUCCESS"
            assert state[4] is None

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


class TestFailureRecording:
    def test_failure_after_success_preserves_coverage_and_records_error(self, conn) -> None:
        outcome = record_fund_success(
            conn, CAND, [_hist("2024-01-02", u=Decimal("1.100000"), a=Decimal("2.200000"))]
        )
        record_fund_failure(conn, CAND, RuntimeError("network timeout"))
        with conn.cursor() as cur:
            cur.execute(
                "SELECT first_data_date, last_data_date, last_sync_status, last_error "
                "FROM fund_sync_state WHERE fund_id=%s",
                (outcome.fund_id,),
            )
            first_d, last_d, status, err = cur.fetchone()
            assert first_d == date(2024, 1, 2)
            assert last_d == date(2024, 1, 2)
            assert status == "FAILED"
            assert "network timeout" in err
        rows = query_nav_range(conn, "000001", date(2024, 1, 1), date(2024, 1, 31))
        assert len(rows) == 1

    def test_failure_redacts_connection_credentials(
        self, conn, monkeypatch
    ) -> None:
        """A last_error message must not contain the DSN or its password."""
        dsn = "postgresql://fofoca_app:s3cr3tP4ss@127.0.0.1:5432/fofoca_test"
        monkeypatch.setenv("FOFOCA_DATABASE_URL", dsn)

        record_fund_failure(
            conn,
            CAND,
            RuntimeError(f"connection failed for {dsn} with password=s3cr3tP4ss"),
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_error FROM fund_sync_state "
                "WHERE fund_id = (SELECT id FROM fund WHERE code='000001')"
            )
            err = cur.fetchone()[0]
        assert "s3cr3tP4ss" not in err
        assert "fofoca_app:s3cr3tP4ss" not in err
        assert "<FOFOCA_DATABASE_URL>" in err

    def test_failure_then_success_clears_error(self, conn) -> None:
        record_fund_failure(conn, CAND, RuntimeError("transient"))
        outcome = record_fund_success(conn, CAND, [_hist("2024-01-02", u=Decimal("1.100000"))])
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_sync_status, last_error FROM fund_sync_state WHERE fund_id=%s",
                (outcome.fund_id,),
            )
            status, err = cur.fetchone()
            assert status == "SUCCESS"
            assert err is None

    def test_error_message_is_sanitized(self, conn) -> None:
        fund_id = record_fund_failure(
            conn,
            CAND,
            RuntimeError("connect postgresql://fofoca_app:SuperSecret@db:5432/fofoca failed"),
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_error FROM fund_sync_state WHERE fund_id=%s", (fund_id,)
            )
            err = cur.fetchone()[0]
            assert "SuperSecret" not in err
            assert "***" in err


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
            for table in ("fund", "fund_nav_daily", "fund_sync_state"):
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name=%s AND table_schema=current_schema()",
                    (table,),
                )
                cols = {r[0] for r in cur.fetchall()}
                assert "fee" not in cols
                assert "手续费" not in cols
