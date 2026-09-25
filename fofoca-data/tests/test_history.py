"""Offline tests for fetching/merging history NAV and reconciling with the daily snapshot."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from fofoca_data.eligibility import parse_daily_snapshot
from fofoca_data.errors import NavDataError
from fofoca_data.history import fetch_fund_nav

from .fixtures import (
    FixtureProvider,
    make_accumulated_nav_df,
    make_daily_df,
    make_unit_nav_df,
)


def _provider(unit, accumulated) -> FixtureProvider:
    return FixtureProvider(
        unit_nav={"000001": unit},
        accumulated_nav={"000001": accumulated},
    )


def _snapshot_for(rows, dated, **kwargs):
    df = make_daily_df(rows, dated=dated, **kwargs)
    return parse_daily_snapshot(df)


class TestHistoryOnlyMerge:
    def test_overlapping_and_disjoint_dates(self) -> None:
        provider = _provider(
            make_unit_nav_df([("2024-01-02", 1.1), ("2024-01-03", 1.2)]),
            make_accumulated_nav_df([("2024-01-03", 2.2), ("2024-01-04", 2.3)]),
        )
        result = fetch_fund_nav(provider, "000001")
        assert not result.partial_coverage
        assert not result.both_empty
        assert result.discrepancies == []
        by_date = {p.nav_date: p for p in result.historical_points}
        assert set(by_date) == {date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)}
        assert by_date[date(2024, 1, 2)].unit_nav == Decimal("1.100000")
        assert by_date[date(2024, 1, 2)].accumulated_nav is None

    def test_unit_empty_accumulated_nonempty_partial_success(self) -> None:
        provider = _provider(
            pd.DataFrame(columns=["净值日期", "单位净值", "日增长率"]),
            make_accumulated_nav_df([("2024-01-04", 2.3)]),
        )
        result = fetch_fund_nav(provider, "000001")
        assert result.partial_coverage
        assert result.unit_indicator_empty
        assert not result.accumulated_indicator_empty

    def test_both_empty_fails_even_with_snapshot(self) -> None:
        provider = _provider(
            pd.DataFrame(columns=["净值日期", "单位净值", "日增长率"]),
            pd.DataFrame(columns=["净值日期", "累计净值"]),
        )
        snapshot = _snapshot_for(
            [("000001", "开放申购", "开放赎回")],
            dated=[("000001", "1.5", "1.6")],
        )
        with pytest.raises(NavDataError):
            fetch_fund_nav(provider, "000001", snapshot)


class TestSnapshotSupplementation:
    def test_snapshot_fills_missing_date(self) -> None:
        """History has only A; snapshot has B -> merged has both."""
        provider = _provider(
            make_unit_nav_df([("2024-01-02", 1.1)]),
            make_accumulated_nav_df([("2024-01-02", 2.1)]),
        )
        snapshot = _snapshot_for(
            [("000001", "开放申购", "开放赎回")],
            dated=[("000001", "1.5", "2.5")],  # 2026-09-24
            date1="2026-09-24",
        )
        result = fetch_fund_nav(provider, "000001", snapshot)
        merged = result.merged_points
        by_date = {p.nav_date: p for p in merged}
        # 历史日期保留
        assert by_date[date(2024, 1, 2)].unit_nav == Decimal("1.100000")
        # 快照日期补入
        assert by_date[date(2026, 9, 24)].unit_nav == Decimal("1.500000")
        assert by_date[date(2026, 9, 24)].accumulated_nav == Decimal("2.500000")
        assert result.discrepancies == []

    def test_snapshot_fills_missing_metric_on_existing_date(self) -> None:
        """History on date A has unit only; snapshot on A has accumulated -> fill it."""
        provider = _provider(
            make_unit_nav_df([("2026-09-24", 1.1)]),
            pd.DataFrame(columns=["净值日期", "累计净值"]),  # empty history acc
        )
        snapshot = _snapshot_for(
            [("000001", "开放申购", "开放赎回")],
            dated=[("000001", None, "2.5")],  # only accumulated on 2026-09-24
            date1="2026-09-24",
        )
        result = fetch_fund_nav(provider, "000001", snapshot)
        assert result.partial_coverage
        merged = result.merged_points
        p = next(pt for pt in merged if pt.nav_date == date(2026, 9, 24))
        assert p.unit_nav == Decimal("1.100000")
        assert p.accumulated_nav == Decimal("2.500000")
        assert result.discrepancies == []

    def test_snapshot_does_not_override_history(self) -> None:
        """Snapshot has 1.5 for a date history says 1.1 -> historical wins, reported."""
        provider = _provider(
            make_unit_nav_df([("2026-09-24", 1.1)]),
            make_accumulated_nav_df([("2026-09-24", 2.1)]),
        )
        snapshot = _snapshot_for(
            [("000001", "开放申购", "开放赎回")],
            dated=[("000001", "1.5", "2.5")],
            date1="2026-09-24",
        )
        result = fetch_fund_nav(provider, "000001", snapshot)
        merged = result.merged_points
        p = next(pt for pt in merged if pt.nav_date == date(2026, 9, 24))
        # Historical values win
        assert p.unit_nav == Decimal("1.100000")
        assert p.accumulated_nav == Decimal("2.100000")
        # Discrepancies reported
        assert len(result.discrepancies) == 2
        metrics = {d.metric for d in result.discrepancies}
        assert metrics == {"unit_nav", "accumulated_nav"}
        for d in result.discrepancies:
            assert d.nav_date == date(2026, 9, 24)
            assert d.historical in (Decimal("1.100000"), Decimal("2.100000"))
            assert d.snapshot in (Decimal("1.500000"), Decimal("2.500000"))

    def test_snapshot_uses_at_most_two_latest_dates(self) -> None:
        """If the daily feed ever exposes 3 distinct dated sets, only the latest 2 count."""
        provider = _provider(
            make_unit_nav_df([("2024-01-02", 1.1)]),
            make_accumulated_nav_df([("2024-01-02", 2.1)]),
        )
        df = make_daily_df(
            [("000001", "开放申购", "开放赎回")],
            dated=[("000001", "1.5", "2.5")],  # date1 = 2026-09-24
            date1="2026-09-24",
            date2="2026-09-23",
            extra_dated_columns={
                "2026-09-22-单位净值": ["9.9"],  # should be ignored (3rd latest)
            },
        )
        snapshot = parse_daily_snapshot(df)
        assert snapshot.dates == [date(2026, 9, 24), date(2026, 9, 23)]
        result = fetch_fund_nav(provider, "000001", snapshot)
        merged_dates = {p.nav_date for p in result.merged_points}
        # 9.9 must not appear (would indicate the 3rd date leaked in)
        for p in result.merged_points:
            assert p.unit_nav != Decimal("9.900000")


class TestSnapshotFailClosed:
    def test_invalid_snapshot_nav_value_fails_fund(self) -> None:
        """A non-positive or non-decimal snapshot value for a selected fund fails it."""
        provider = _provider(
            make_unit_nav_df([("2024-01-02", 1.1)]),
            make_accumulated_nav_df([("2024-01-02", 2.1)]),
        )
        snapshot = _snapshot_for(
            [("000001", "开放申购", "开放赎回")],
            dated=[("000001", "0", "2.5")],  # non-positive unit
            date1="2026-09-24",
        )
        with pytest.raises(NavDataError):
            fetch_fund_nav(provider, "000001", snapshot)


class TestHistoryValidation:
    def test_request_failure_unit(self) -> None:
        provider = _provider(
            RuntimeError("network down"),
            make_accumulated_nav_df([("2024-01-04", 2.3)]),
        )
        with pytest.raises(NavDataError):
            fetch_fund_nav(provider, "000001")

    def test_request_failure_accumulated(self) -> None:
        provider = _provider(
            make_unit_nav_df([("2024-01-02", 1.1)]),
            RuntimeError("network down"),
        )
        with pytest.raises(NavDataError):
            fetch_fund_nav(provider, "000001")

    def test_malformed_history_date_rejected(self) -> None:
        bad = make_unit_nav_df([("not-a-date", 1.1)])
        provider = _provider(bad, make_accumulated_nav_df([("2024-01-04", 2.3)]))
        with pytest.raises(NavDataError):
            fetch_fund_nav(provider, "000001")

    def test_nonpositive_history_nav_rejected(self) -> None:
        bad = make_unit_nav_df([("2024-01-02", 0.0)])
        provider = _provider(bad, make_accumulated_nav_df([("2024-01-04", 2.3)]))
        with pytest.raises(NavDataError):
            fetch_fund_nav(provider, "000001")

    def test_duplicate_history_date_rejected(self) -> None:
        bad = make_unit_nav_df([("2024-01-02", 1.1), ("2024-01-02", 1.2)])
        provider = _provider(bad, make_accumulated_nav_df([("2024-01-04", 2.3)]))
        with pytest.raises(NavDataError):
            fetch_fund_nav(provider, "000001")

    def test_float_precision_half_up(self) -> None:
        provider = _provider(
            make_unit_nav_df([("2024-01-02", 1.2345675)]),
            make_accumulated_nav_df([("2024-01-02", 2.0000005)]),
        )
        result = fetch_fund_nav(provider, "000001")
        p = result.historical_points[0]
        assert p.unit_nav == Decimal("1.234568")
        assert p.accumulated_nav == Decimal("2.000001")
