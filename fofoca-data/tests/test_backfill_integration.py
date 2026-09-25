"""Integration tests for the backfill orchestrator against a disposable DB."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from fofoca_data.backfill import run_backfill
from fofoca_data.db import apply_schema, query_nav_range

from .fixtures import (
    FixtureProvider,
    make_accumulated_nav_df,
    make_daily_df,
    make_name_df,
    make_unit_nav_df,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("FOFOCA_INTEGRATION_DB") != "true"
    or not os.environ.get("FOFOCA_TEST_DATABASE_URL"),
    reason="disposable DB integration disabled",
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


def _provider_two_funds() -> FixtureProvider:
    name_df = make_name_df(
        [
            ("000001", "基金A", "混合型-灵活"),
            ("000002", "基金B", "混合型-灵活"),
            ("000003", "基金C", "混合型-灵活"),
            ("166009", "中欧新动力LOF", "混合型-偏股"),
        ]
    )
    daily_df = make_daily_df(
        [
            ("000001", "开放申购", "开放赎回"),
            ("000002", "开放申购", "开放赎回"),
            ("000003", "开放申购", "开放赎回"),
            ("166009", "开放申购", "开放赎回"),
        ],
        dated=[
            ("000001", "1.5", "2.5"),
            ("000002", "1.0", "1.0"),
            ("000003", "3.5", "4.5"),
            ("166009", "3.4483", "4.4903"),
        ],
    )
    return FixtureProvider(
        name_df=name_df,
        daily_df=daily_df,
        unit_nav={
            "000001": make_unit_nav_df([("2024-01-02", 1.1), ("2024-01-03", 1.2)]),
            "000002": RuntimeError("simulated provider failure"),
            "000003": make_unit_nav_df([("2024-02-01", 3.1)]),
            "166009": make_unit_nav_df([("2024-01-02", 3.4)]),
        },
        accumulated_nav={
            "000001": make_accumulated_nav_df([("2024-01-03", 2.2)]),
            "000002": make_accumulated_nav_df([("2024-01-03", 2.2)]),
            "000003": make_accumulated_nav_df([("2024-02-01", 4.1)]),
            "166009": make_accumulated_nav_df([("2024-01-02", 4.4)]),
        },
    )


class TestBackfillAllFunds:
    def test_all_funds_continues_after_failure(self, conn) -> None:
        provider = _provider_two_funds()
        report = run_backfill(conn, provider, supplied_codes=None)

        assert report.selection_error is None
        assert report.succeeded == 3  # 000001, 000003, 166009
        assert report.failed == 1     # 000002
        assert not report.ok

        by_code = {r.candidate.code: r for r in report.fund_results}
        assert by_code["000001"].status == "SUCCESS"
        assert by_code["000001"].partial_coverage is False
        # 000001 history has 2024-01-02 and 2024-01-03; snapshot 2026-09-24 fills.
        # snapshot dates differ from history dates -> snapshot_fill_count == 2 (unit+acc on snapshot date)
        assert by_code["000001"].snapshot_fill_count == 2
        assert by_code["000001"].stored_rows == 3  # 2 history dates + 1 snapshot date

        assert by_code["000002"].status == "FAILED"
        assert "simulated provider failure" in by_code["000002"].error

        assert by_code["000003"].status == "SUCCESS"
        assert by_code["166009"].status == "SUCCESS"

        # Successful funds persisted
        rows1 = query_nav_range(conn, "000001", date(2024, 1, 1), date(2030, 12, 31))
        assert len(rows1) == 3
        rows3 = query_nav_range(conn, "000003", date(2024, 1, 1), date(2030, 12, 31))
        assert len(rows3) == 2
        rows9 = query_nav_range(conn, "166009", date(2024, 1, 1), date(2030, 12, 31))
        assert len(rows9) == 2

        # Failed fund: no NAV rows, but sync state recorded as FAILED
        rows2 = query_nav_range(conn, "000002", date(2024, 1, 1), date(2030, 12, 31))
        assert rows2 == []
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_sync_status, last_error FROM fund_sync_state s "
                "JOIN fund f ON f.id = s.fund_id WHERE f.code='000002'"
            )
            status, err = cur.fetchone()
            assert status == "FAILED"
            assert "simulated provider failure" in err

    def test_rerun_idempotent(self, conn) -> None:
        provider = _provider_two_funds()
        run_backfill(conn, provider, supplied_codes=None)
        run_backfill(conn, provider, supplied_codes=None)
        rows = query_nav_range(conn, "000001", date(2024, 1, 1), date(2030, 12, 31))
        assert len(rows) == 3  # unchanged

    def test_snapshot_does_not_override_existing_stored(self, conn) -> None:
        """After initial run, a rerun with the same snapshot must leave stored values alone."""
        provider = _provider_two_funds()
        run_backfill(conn, provider, supplied_codes=None)
        # Tamper with one row to verify the snapshot does not "fix" it back
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE fund_nav_daily SET unit_nav = %s "
                "WHERE fund_id=(SELECT id FROM fund WHERE code='166009') AND nav_date=%s",
                (Decimal("9.999999"), date(2026, 9, 24)),
            )
        # Rerun; snapshot for 166009 has unit 3.4483 on 2026-09-24 but stored non-NULL 9.999999 stays
        run_backfill(conn, provider, supplied_codes=None)
        rows = query_nav_range(conn, "166009", date(2026, 9, 24), date(2026, 9, 24))
        assert rows[0].unit_nav == Decimal("9.999999")


class TestBackfillSuppliedCodes:
    def test_supplied_codes_only(self, conn) -> None:
        provider = _provider_two_funds()
        report = run_backfill(conn, provider, supplied_codes=["000001"])
        assert report.succeeded == 1
        assert report.failed == 0
        assert report.ok
        rows = query_nav_range(conn, "000001", date(2024, 1, 1), date(2030, 12, 31))
        assert len(rows) == 3
        rows3 = query_nav_range(conn, "000003", date(2024, 1, 1), date(2030, 12, 31))
        assert rows3 == []

    def test_ineligible_supplied_code_not_fetched(self, conn) -> None:
        provider = _provider_two_funds()
        report = run_backfill(conn, provider, supplied_codes=["999999"])
        assert report.succeeded == 0
        assert report.failed == 0
        assert report.ok
        assert report.selection.ineligible_supplied_codes == ["999999"]


class TestSelectionFailure:
    def test_daily_feed_failure_aborts_before_any_nav(self, conn) -> None:
        provider = _provider_two_funds()
        provider.daily_df = RuntimeError("daily feed unavailable")
        report = run_backfill(conn, provider, supplied_codes=None)
        assert report.selection is None
        assert report.selection_error is not None
        assert "daily" in report.selection_error.lower()
        assert report.fund_results == []
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fund")
            assert cur.fetchone()[0] == 0


class TestDiscrepancyReporting:
    def test_discrepancy_reported_when_snapshot_differs_from_history(self, conn) -> None:
        """History says 1.1 for 2026-09-24, snapshot says 1.5 -> history wins, report."""
        name_df = make_name_df([("000001", "基金A", "混合型-灵活")])
        daily_df = make_daily_df(
            [("000001", "开放申购", "开放赎回")],
            dated=[("000001", "1.5", "2.5")],
        )
        provider = FixtureProvider(
            name_df=name_df,
            daily_df=daily_df,
            unit_nav={"000001": make_unit_nav_df([("2026-09-24", 1.1)])},
            accumulated_nav={"000001": make_accumulated_nav_df([("2026-09-24", 2.1)])},
        )
        report = run_backfill(conn, provider, supplied_codes=None)
        assert report.ok
        r = report.fund_results[0]
        assert r.status == "SUCCESS"
        assert r.discrepancy_count == 2
        metrics = {d["metric"] for d in r.discrepancies}
        assert metrics == {"unit_nav", "accumulated_nav"}
        for d in r.discrepancies:
            assert d["nav_date"] == "2026-09-24"
        # Stored values are the historical ones
        rows = query_nav_range(conn, "000001", date(2026, 9, 24), date(2026, 9, 24))
        assert rows[0].unit_nav == Decimal("1.100000")
        assert rows[0].accumulated_nav == Decimal("2.100000")


class TestPartialCoverage:
    def test_partial_indicator_marks_success_with_flag(self, conn) -> None:
        import pandas as pd

        name_df = make_name_df([("000001", "基金A", "混合型-灵活")])
        daily_df = make_daily_df(
            [("000001", "开放申购", "开放赎回")],
            dated=[("000001", "1.5", "2.5")],
        )
        provider = FixtureProvider(
            name_df=name_df,
            daily_df=daily_df,
            unit_nav={"000001": make_unit_nav_df([("2024-01-02", 1.1)])},
            accumulated_nav={"000001": pd.DataFrame()},
        )
        report = run_backfill(conn, provider, supplied_codes=None)
        assert report.ok
        r = report.fund_results[0]
        assert r.status == "SUCCESS"
        assert r.partial_coverage is True


class TestUnexpectedPerFundError:
    """A single fund raising an unexpected exception must NOT abort the run."""

    def test_invalid_operation_from_provider_fails_only_that_fund(self, conn) -> None:
        """An unexpected exception raised from inside the provider must be
        caught by the orchestrator's per-fund catch-all and the run must
        continue. We monkeypatch ``fetch_fund_nav`` directly because the
        Provider-protocol wrapper normalises provider-raised exceptions to
        ``NavDataError`` (which is by design)."""
        import fofoca_data.backfill as backfill_mod
        from decimal import InvalidOperation

        provider = _provider_two_funds()

        real_fetch = backfill_mod.fetch_fund_nav

        def fake_fetch(prov, code, snapshot=None):
            if code == "000001":
                raise InvalidOperation("bad Decimal in provider")
            return real_fetch(prov, code, snapshot)

        backfill_mod.fetch_fund_nav = fake_fetch
        try:
            report = run_backfill(conn, provider, supplied_codes=None)
        finally:
            backfill_mod.fetch_fund_nav = real_fetch

        assert not report.ok  # at least one failure
        # Fixture has 000002 with a pre-existing simulated provider failure,
        # so: 000001 fails (InvalidOperation), 000002 fails (RuntimeError),
        # 000003 + 166009 succeed.
        assert report.succeeded == 2
        assert report.failed == 2

        failed = next(r for r in report.fund_results if r.candidate.code == "000001")
        assert failed.status == "FAILED"
        assert "unexpected" in failed.error.lower() or "InvalidOperation" in failed.error

        succeeded = next(r for r in report.fund_results if r.candidate.code == "000003")
        assert succeeded.status == "SUCCESS"

        # 000003's rows were written
        rows = query_nav_range(conn, "000003", date(2020, 1, 1), date(2030, 12, 31))
        assert rows

        # 000001 has FAILED sync state
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_sync_status, last_error FROM fund_sync_state "
                "WHERE fund_id = (SELECT id FROM fund WHERE code='000001')"
            )
            row = cur.fetchone()
        assert row[0] == "FAILED"
        assert "InvalidOperation" in row[1] or "bad Decimal" in row[1]

    def test_database_loss_raises_database_error(self, conn) -> None:
        """If the DB connection is broken mid-run, the orchestrator must
        abort with DatabaseError rather than silently skip funds."""
        import psycopg

        from fofoca_data.errors import DatabaseError

        provider = _provider_two_funds()
        conn.close()  # simulate lost connection

        with pytest.raises((DatabaseError, psycopg.OperationalError, psycopg.InterfaceError)):
            run_backfill(conn, provider, supplied_codes=None)


class TestErrorRedaction:
    """Any text persisted or reported must not contain credentials."""

    def test_per_fund_error_redacts_connection_string(self, conn, monkeypatch) -> None:
        dsn = "postgresql://fofoca_app:s3cr3tP4ss@127.0.0.1:5432/fofoca_test"
        monkeypatch.setenv("FOFOCA_DATABASE_URL", dsn)

        provider = _provider_two_funds()
        provider.unit_nav["000001"] = RuntimeError(
            f"upstream exploded for {dsn} with password=s3cr3tP4ss"
        )
        provider.accumulated_nav["000001"] = RuntimeError(
            f"upstream exploded for {dsn} with password=s3cr3tP4ss"
        )

        report = run_backfill(conn, provider, supplied_codes=None)
        failed = next(r for r in report.fund_results if r.candidate.code == "000001")
        assert "s3cr3tP4ss" not in (failed.error or "")
        assert "<FOFOCA_DATABASE_URL>" in (failed.error or "")

        # And the persisted last_error is also redacted
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_error FROM fund_sync_state "
                "WHERE fund_id = (SELECT id FROM fund WHERE code='000001')"
            )
            persisted = cur.fetchone()[0]
        assert "s3cr3tP4ss" not in persisted
