"""Integration tests for the backfill orchestrator against a disposable DB."""

from __future__ import annotations

import io
import os
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from fofoca_data.backfill import ProgressReporter, run_backfill
from fofoca_data.db import (
    NavWrite,
    apply_schema,
    query_nav_range,
    record_fund_success,
)
from fofoca_data.eligibility import FundCandidate

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

TARGET = date(2026, 9, 24)


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


def _progress() -> tuple[ProgressReporter, io.StringIO]:
    buf = io.StringIO()
    return ProgressReporter(buf), buf


class TestBackfillAllFunds:
    def test_all_funds_continues_after_failure(self, conn) -> None:
        provider = _provider_two_funds()
        progress, buf = _progress()
        report = run_backfill(conn, provider, supplied_codes=None, progress=progress)

        assert report.selection_error is None
        assert report.succeeded == 3  # 000001, 000003, 166009
        assert report.failed == 1     # 000002
        assert report.attempted == 4
        assert report.skipped == 0
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
        assert by_code["000002"].error_class == "fetch"

        assert by_code["000003"].status == "SUCCESS"
        assert by_code["166009"].status == "SUCCESS"

        # Live progress was emitted before the final report.
        live = buf.getvalue()
        assert "START code=000001" in live
        assert "indicator=单位净值走势" in live
        assert "FAILED code=000002" in live
        assert "SUCCESS code=000003" in live

        # Successful funds persisted
        rows1 = query_nav_range(conn, "000001", date(2024, 1, 1), date(2030, 12, 31))
        assert len(rows1) == 3
        rows3 = query_nav_range(conn, "000003", date(2024, 1, 1), date(2030, 12, 31))
        assert len(rows3) == 2
        rows9 = query_nav_range(conn, "166009", date(2024, 1, 1), date(2030, 12, 31))
        assert len(rows9) == 2

        # Failed fund: NO NAV rows, NO fund row (no failure-only fund upsert),
        # and no state table exists at all.
        rows2 = query_nav_range(conn, "000002", date(2024, 1, 1), date(2030, 12, 31))
        assert rows2 == []
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fund WHERE code='000002'")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT to_regclass('fund_sync_state')")
            assert cur.fetchone()[0] is None

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

    def test_missing_cell_does_not_erase_stored_value(self, conn) -> None:
        """A later NaN historical cell leaves the stored non-NULL value intact."""
        import pandas as pd

        name_df = make_name_df([("000001", "基金A", "混合型-灵活")])
        daily_df = make_daily_df(
            [("000001", "开放申购", "开放赎回")],
            dated=[("000001", "1.5", "2.5")],
        )
        good = FixtureProvider(
            name_df=name_df,
            daily_df=daily_df,
            unit_nav={"000001": make_unit_nav_df([("2024-01-02", 1.1), ("2024-01-03", 1.2)])},
            accumulated_nav={"000001": make_accumulated_nav_df([("2024-01-03", 2.2)])},
        )
        run_backfill(conn, good, supplied_codes=None)
        rows = query_nav_range(conn, "000001", date(2024, 1, 1), date(2030, 1, 1))
        assert len(rows) == 3  # 2 history dates + 1 snapshot-fill date

        # Second run: unit indicator now has NaN on 2024-01-03 (was 1.2 before).
        degraded = FixtureProvider(
            name_df=name_df,
            daily_df=daily_df,
            unit_nav={
                "000001": pd.DataFrame(
                    {
                        "净值日期": ["2024-01-02", "2024-01-03"],
                        "单位净值": [1.1, float("nan")],
                        "日增长率": [0.0, None],
                    }
                )
            },
            accumulated_nav={"000001": make_accumulated_nav_df([("2024-01-03", 2.2)])},
        )
        report = run_backfill(conn, degraded, supplied_codes=None)
        assert report.ok
        rows = {
            r.nav_date: r
            for r in query_nav_range(conn, "000001", date(2024, 1, 1), date(2030, 1, 1))
        }
        # Stored non-NULL 1.2 on 2024-01-03 was NOT erased by the NaN cell.
        assert rows[date(2024, 1, 3)].unit_nav == Decimal("1.200000")
        assert rows[date(2024, 1, 3)].accumulated_nav == Decimal("2.200000")

    def test_both_unusable_histories_fail_despite_snapshot(self, conn) -> None:
        import pandas as pd

        name_df = make_name_df([("000001", "基金A", "混合型-灵活")])
        daily_df = make_daily_df(
            [("000001", "开放申购", "开放赎回")],
            dated=[("000001", "1.5", "2.5")],
        )
        provider = FixtureProvider(
            name_df=name_df,
            daily_df=daily_df,
            unit_nav={
                "000001": pd.DataFrame(
                    {"净值日期": ["2024-01-02"], "单位净值": [float("nan")], "日增长率": [None]}
                )
            },
            accumulated_nav={
                "000001": pd.DataFrame({"净值日期": ["2024-01-02"], "累计净值": [None]})
            },
        )
        report = run_backfill(conn, provider, supplied_codes=None)
        assert report.failed == 1
        assert not report.ok
        r = report.fund_results[0]
        assert r.status == "FAILED"
        assert r.error_class == "validation"
        # Nothing persisted, not even a fund row.
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fund")
            assert cur.fetchone()[0] == 0


class TestTargetDate:
    """--target-date behavior against disposable PG."""

    def _seed(self, conn, code: str, nav_date: date | None) -> None:
        if nav_date is None:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO fund (code, name, fund_type) VALUES (%s, 'X', '混合型') "
                    "ON CONFLICT (code) DO NOTHING",
                    (code,),
                )
            return
        record_fund_success(
            conn,
            FundCandidate(code=code, name="X", fund_type="混合型"),
            [NavWrite(nav_date=nav_date, historical_unit_nav=Decimal("1.234567"))],
        )

    def test_skip_when_max_equal_target_no_fetch_no_write(self, conn) -> None:
        self._seed(conn, "000001", TARGET)
        provider = _provider_two_funds()
        report = run_backfill(
            conn, provider, supplied_codes=["000001"], target_date=TARGET
        )
        assert report.ok
        r = report.fund_results[0]
        assert r.status == "SKIPPED"
        assert r.stored_max_nav_date == TARGET
        assert report.skipped == 1
        assert report.attempted == 0
        assert report.below_target_codes == []
        # Stored row untouched.
        rows = query_nav_range(conn, "000001", TARGET, TARGET)
        assert rows[0].unit_nav == Decimal("1.234567")

    def test_skip_when_max_after_target(self, conn) -> None:
        self._seed(conn, "000001", date(2026, 9, 25))
        provider = _provider_two_funds()
        report = run_backfill(
            conn, provider, supplied_codes=["000001"], target_date=TARGET
        )
        assert report.fund_results[0].status == "SKIPPED"
        assert report.ok

    def test_attempt_when_max_before_target(self, conn) -> None:
        self._seed(conn, "000001", date(2026, 9, 23))
        provider = _provider_two_funds()
        report = run_backfill(
            conn, provider, supplied_codes=["000001"], target_date=TARGET
        )
        r = report.fund_results[0]
        assert r.status == "SUCCESS"
        assert report.attempted == 1
        # Snapshot 2026-09-24 fills -> fund reaches target after the run.
        assert report.below_target_codes == []
        assert report.ok
        rows = query_nav_range(conn, "000001", date(2020, 1, 1), date(2030, 1, 1))
        assert max(x.nav_date for x in rows) == TARGET

    def test_attempt_when_no_nav_rows(self, conn) -> None:
        self._seed(conn, "000001", None)  # fund row, no NAV
        provider = _provider_two_funds()
        report = run_backfill(
            conn, provider, supplied_codes=["000001"], target_date=TARGET
        )
        assert report.fund_results[0].status == "SUCCESS"
        assert report.below_target_codes == []

    def test_attempt_when_no_fund_row(self, conn) -> None:
        provider = _provider_two_funds()
        report = run_backfill(
            conn, provider, supplied_codes=["000001"], target_date=TARGET
        )
        assert report.fund_results[0].status == "SUCCESS"

    def test_successful_attempt_still_below_target_reported_not_ok(self, conn) -> None:
        """Fetched history ends before T: attempt SUCCESS, code listed below
        target, run NOT ok (no false completion)."""
        name_df = make_name_df([("000001", "基金A", "混合型-灵活")])
        # Daily feed dated 2026-09-23 (below target): no snapshot fill to T.
        daily_df = make_daily_df(
            [("000001", "开放申购", "开放赎回")],
            dated=[("000001", "1.5", "2.5")],
            date1="2026-09-23",
            date2="2026-09-22",
        )
        provider = FixtureProvider(
            name_df=name_df,
            daily_df=daily_df,
            unit_nav={"000001": make_unit_nav_df([("2026-09-20", 1.1)])},
            accumulated_nav={"000001": make_accumulated_nav_df([("2026-09-20", 2.1)])},
        )
        progress, buf = _progress()
        report = run_backfill(
            conn, provider, supplied_codes=["000001"], target_date=TARGET,
            progress=progress,
        )
        r = report.fund_results[0]
        assert r.status == "SUCCESS"
        assert report.succeeded == 1
        assert report.failed == 0
        assert report.below_target_codes == ["000001"]
        assert not report.ok  # shortfall -> nonzero exit, no false SUCCESS
        assert "RECONCILIATION" in buf.getvalue()

    def test_failed_attempt_listed_below_target_and_failed(self, conn) -> None:
        provider = _provider_two_funds()
        report = run_backfill(
            conn, provider, supplied_codes=["000002"], target_date=TARGET
        )
        r = report.fund_results[0]
        assert r.status == "FAILED"
        assert report.failed == 1
        assert report.below_target_codes == ["000002"]
        assert not report.ok

    def test_mixed_skip_attempt_outcomes(self, conn) -> None:
        self._seed(conn, "000001", date(2026, 9, 24))
        provider = _provider_two_funds()
        report = run_backfill(
            conn,
            provider,
            supplied_codes=["000001", "000002", "000003"],
            target_date=TARGET,
        )
        by_code = {r.candidate.code: r for r in report.fund_results}
        assert by_code["000001"].status == "SKIPPED"
        assert by_code["000002"].status == "FAILED"
        assert by_code["000003"].status == "SUCCESS"
        assert report.skipped == 1
        assert report.attempted == 2
        assert report.succeeded == 1
        assert report.failed == 1
        # 000003 reaches T via snapshot; 000002 stays below.
        assert report.below_target_codes == ["000002"]
        assert not report.ok

    def test_no_target_still_full_refetch_even_when_max_later(self, conn) -> None:
        """Without --target-date, a fund whose stored max is already later
        than the recovery target is still re-fetched (ordinary recheck)."""
        self._seed(conn, "000001", date(2026, 12, 31))
        provider = _provider_two_funds()
        report = run_backfill(conn, provider, supplied_codes=["000001"])
        r = report.fund_results[0]
        assert r.status == "SUCCESS"  # attempted, not skipped
        assert report.below_target_codes is None  # reconciliation only in target mode
        rows = query_nav_range(conn, "000001", date(2020, 1, 1), date(2030, 1, 1))
        assert len(rows) > 1

    def test_lookup_failure_aborts_run(self, conn) -> None:
        from fofoca_data.errors import DatabaseError

        provider = _provider_two_funds()
        conn.close()
        with pytest.raises(DatabaseError):
            run_backfill(conn, provider, supplied_codes=["000001"], target_date=TARGET)


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

        def fake_fetch(prov, code, snapshot=None, progress=None):
            if code == "000001":
                raise InvalidOperation("bad Decimal in provider")
            return real_fetch(prov, code, snapshot, progress=progress)

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

        # The failed funds left no fund row and no NAV (no failure-state writes).
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fund WHERE code IN ('000001', '000002')")
            assert cur.fetchone()[0] == 0

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
    """Any text reported must not contain credentials."""

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

        progress, buf = _progress()
        report = run_backfill(conn, provider, supplied_codes=None, progress=progress)
        failed = next(r for r in report.fund_results if r.candidate.code == "000001")
        assert "s3cr3tP4ss" not in (failed.error or "")
        assert "<FOFOCA_DATABASE_URL>" in (failed.error or "")
        # Live progress output is redacted too.
        assert "s3cr3tP4ss" not in buf.getvalue()
