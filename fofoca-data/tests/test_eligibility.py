"""Offline tests for the eligibility selector."""

from __future__ import annotations

import pandas as pd
import pytest

from fofoca_data.eligibility import select_candidates
from fofoca_data.errors import SelectionError

from .fixtures import FixtureProvider, make_daily_df, make_name_df


def _base_provider(**overrides) -> FixtureProvider:
    """A small deterministic catalog exercising every filter branch.

    Codes:
      000001 - 混合型-灵活, open/open -> ELIGIBLE
      000002 - 指数型-股票, open/open -> ELIGIBLE (ETF feeder, no spot check)
      166009 - 混合型-偏股 LOF, open/open -> ELIGIBLE (spec example)
      000004 - 指数型-股票, open/open -> ELIGIBLE (would previously have been
               rejected by spot feeds; now selected as "may be ETF")
      000005 - 货币型, open/open -> excluded by money-market type
      000006 - 申购状态=封闭期 -> excluded by purchase status
      000007 - 赎回状态=暂停赎回 -> excluded by redemption status
      000008 - in daily only, not in name -> excluded by intersection
      000009 - in name only, not in daily -> excluded by intersection
    """
    name_df = make_name_df(
        [
            ("000001", "华夏成长混合", "混合型-灵活"),
            ("000002", "易方达沪深300ETF联接A", "指数型-股票"),
            ("166009", "中欧新动力混合(LOF)A", "混合型-偏股"),
            ("000004", "某双开放指数基金", "指数型-股票"),
            ("000005", "某货币基金A", "货币型"),
            ("000006", "封闭期基金", "混合型-灵活"),
            ("000007", "暂停赎回基金", "混合型-灵活"),
            ("000009", "只在名称目录", "混合型-灵活"),
        ]
    )
    daily_df = make_daily_df(
        [
            ("000001", "开放申购", "开放赎回"),
            ("000002", "开放申购", "开放赎回"),
            ("166009", "开放申购", "开放赎回"),
            ("000004", "开放申购", "开放赎回"),
            ("000005", "开放申购", "开放赎回"),
            ("000006", "封闭期", "开放赎回"),
            ("000007", "开放申购", "暂停赎回"),
            ("000008", "开放申购", "开放赎回"),
        ],
        dated=[
            ("000001", "1.295", "3.868"),
            ("000002", "2.1741", "2.2741"),
            ("166009", "3.4483", "4.4903"),
            ("000004", "1.5", "1.5"),
            ("000005", "1.0", "1.0"),
            ("000006", "1.0", "1.0"),
            ("000007", "1.0", "1.0"),
            ("000008", "1.0", "1.0"),
        ],
    )
    kwargs = dict(name_df=name_df, daily_df=daily_df)
    kwargs.update(overrides)
    return FixtureProvider(**kwargs)


class TestAllFundsSelection:
    def test_selects_expected_candidates_and_counts(self) -> None:
        provider = _base_provider()
        result = select_candidates(provider, supplied_codes=None)

        codes = [c.code for c in result.candidates]
        assert codes == ["000001", "000002", "000004", "166009"]

        counts = result.counts.as_dict()
        assert counts["name_directory_size"] == 8
        assert counts["daily_directory_size"] == 8
        # No blank rows in the fixture -> skipped counters are zero
        assert counts["name_rows_skipped_missing_type"] == 0
        assert counts["daily_rows_skipped_missing_status"] == 0
        assert counts["intersection_size"] == 7  # 000001..000007 minus 000008/000009
        assert counts["excluded_purchase_status"] == 1  # 000006
        assert counts["excluded_redemption_status"] == 1  # 000007
        assert counts["excluded_money_market"] == 1  # 000005
        assert counts["final_candidates"] == 4
        assert counts["supplied_ineligible"] == 0
        assert result.ineligible_supplied_codes == []

    def test_blank_rows_counted_separately(self) -> None:
        """Rows dropped because a required field is blank still count toward
        the raw feed size and are surfaced in dedicated skipped counters."""
        name_df = pd.DataFrame(
            {
                "基金代码": ["000001", "000002", "000003"],
                "基金简称": ["A", "B", "C"],
                "基金类型": ["混合型", "", "股票型"],  # 000002 blank -> dropped
            }
        )
        daily_df = pd.DataFrame(
            {
                "基金代码": ["000001", "000002", "000003"],
                "基金简称": ["A", "B", "C"],
                "2026-09-24-单位净值": ["1.0", "1.0", "1.0"],
                "2026-09-24-累计净值": ["1.0", "1.0", "1.0"],
                "申购状态": ["开放申购", "", "开放申购"],  # 000002 blank -> dropped
                "赎回状态": ["开放赎回", "开放赎回", "开放赎回"],
            }
        )
        result = select_candidates(FixtureProvider(name_df=name_df, daily_df=daily_df), None)
        counts = result.counts.as_dict()
        assert counts["name_directory_size"] == 3
        assert counts["daily_directory_size"] == 3
        assert counts["name_rows_skipped_missing_type"] == 1
        assert counts["daily_rows_skipped_missing_status"] == 1
        # Both 000001 and 000003 survive with valid rows.
        assert counts["intersection_size"] == 2
        assert counts["final_candidates"] == 2

    def test_dual_open_lof_is_eligible(self) -> None:
        """166009 (spec example) must be eligible; no name/spot exclusion."""
        provider = _base_provider()
        result = select_candidates(provider, None)
        lof = next(c for c in result.candidates if c.code == "166009")
        assert lof.fund_type == "混合型-偏股"

    def test_dual_open_etf_label_eligible(self) -> None:
        """An ETF-labelled index share with both sides open qualifies (no spot check)."""
        provider = _base_provider()
        result = select_candidates(provider, None)
        codes = {c.code for c in result.candidates}
        # Both 000002 (ETF feeder) and 000004 (index, possibly ETF) qualify.
        assert "000002" in codes
        assert "000004" in codes

    def test_money_market_excluded(self) -> None:
        provider = _base_provider()
        result = select_candidates(provider, None)
        assert "000005" not in {c.code for c in result.candidates}


class TestExplicitCodesSelection:
    def test_mixed_eligible_and_ineligible(self) -> None:
        provider = _base_provider()
        result = select_candidates(provider, ["000001", "000006", "999999"])

        codes = [c.code for c in result.candidates]
        assert codes == ["000001"]
        assert result.ineligible_supplied_codes == ["000006", "999999"]
        assert result.counts.supplied_ineligible == 2
        assert result.counts.final_candidates == 1

    def test_supplied_dual_open_lof_selected(self) -> None:
        provider = _base_provider()
        result = select_candidates(provider, ["166009"])
        assert [c.code for c in result.candidates] == ["166009"]
        assert result.ineligible_supplied_codes == []

    def test_supplied_codes_do_not_bypass_eligibility(self) -> None:
        provider = _base_provider()
        result = select_candidates(provider, ["000005"])  # money-market
        assert result.candidates == []
        assert result.ineligible_supplied_codes == ["000005"]

    def test_duplicate_supplied_codes_deduplicated(self) -> None:
        provider = _base_provider()
        result = select_candidates(provider, ["000001", "000001", "166009"])
        assert [c.code for c in result.candidates] == ["000001", "166009"]


class TestFailClosed:
    def test_name_directory_failure_aborts(self) -> None:
        provider = _base_provider(name_df=RuntimeError("boom"))
        with pytest.raises(SelectionError):
            select_candidates(provider, None)

    def test_daily_feed_failure_aborts(self) -> None:
        provider = _base_provider(daily_df=RuntimeError("boom"))
        with pytest.raises(SelectionError):
            select_candidates(provider, None)

    def test_daily_feed_failure_aborts_explicit_codes(self) -> None:
        """Daily failure must abort even when the user supplied codes."""
        provider = _base_provider(daily_df=RuntimeError("boom"))
        with pytest.raises(SelectionError):
            select_candidates(provider, ["000001"])

    def test_missing_name_column_rejected(self) -> None:
        bad = pd.DataFrame({"基金代码": ["000001"], "基金简称": ["X"]})  # no 基金类型
        provider = _base_provider(name_df=bad)
        with pytest.raises(SelectionError):
            select_candidates(provider, None)

    def test_missing_purchase_status_column_rejected(self) -> None:
        # Drop the 申购状态 column entirely
        bad = pd.DataFrame(
            {
                "基金代码": ["000001"],
                "基金简称": ["X"],
                "赎回状态": ["开放赎回"],
                "2026-09-24-单位净值": ["1.0"],
            }
        )
        provider = _base_provider(daily_df=bad)
        with pytest.raises(SelectionError):
            select_candidates(provider, None)

    def test_no_dated_nav_columns_rejected(self) -> None:
        bad = pd.DataFrame(
            {
                "基金代码": ["000001"],
                "基金简称": ["X"],
                "申购状态": ["开放申购"],
                "赎回状态": ["开放赎回"],
            }
        )
        provider = _base_provider(daily_df=bad)
        with pytest.raises(SelectionError):
            select_candidates(provider, None)

    def test_malformed_dated_column_rejected(self) -> None:
        """A column that *looks* dated but has an invalid calendar date fails."""
        bad = make_daily_df(
            [("000001", "开放申购", "开放赎回")],
            dated=[("000001", "1.0", "1.0")],
            extra_dated_columns={
                "2026-13-01-单位净值": ["1.0"],  # month 13 -> invalid
            },
        )
        provider = _base_provider(daily_df=bad)
        with pytest.raises(SelectionError):
            select_candidates(provider, None)

    def test_blank_purchase_status_excludes_row_but_does_not_abort(self) -> None:
        """A single row with blank status is excluded from candidacy, not fatal."""
        bad = make_daily_df(
            [
                ("000001", "", "开放赎回"),  # blank purchase -> row excluded
                ("000002", "开放申购", "开放赎回"),
            ],
            dated=[
                ("000001", "1.0", "1.0"),
                ("000002", "1.0", "1.0"),
            ],
        )
        name_df = make_name_df(
            [
                ("000001", "X", "混合型-灵活"),
                ("000002", "Y", "混合型-灵活"),
            ]
        )
        provider = FixtureProvider(name_df=name_df, daily_df=bad)
        result = select_candidates(provider, None)
        # 000001 has blank purchase -> excluded by intersection; 000002 eligible
        assert [c.code for c in result.candidates] == ["000002"]

    def test_blank_fund_type_excludes_row_but_does_not_abort(self) -> None:
        """A row with blank 基金类型 in fund_name_em is skipped, not fatal."""
        import pandas as pd

        name_df = pd.DataFrame(
            {
                "基金代码": ["000001", "000002"],
                "拼音缩写": ["XX", "YY"],
                "基金简称": ["X", "Y"],
                "基金类型": ["", "混合型-灵活"],  # 000001 has blank type
                "拼音全称": ["XX", "YY"],
            }
        )
        daily_df = make_daily_df(
            [
                ("000001", "开放申购", "开放赎回"),
                ("000002", "开放申购", "开放赎回"),
            ],
            dated=[("000001", "1.0", "1.0"), ("000002", "1.0", "1.0")],
        )
        provider = FixtureProvider(name_df=name_df, daily_df=daily_df)
        result = select_candidates(provider, None)
        # 000001 has no known type -> excluded; 000002 eligible
        assert [c.code for c in result.candidates] == ["000002"]


class TestDeterministicCounts:
    def test_code_failing_two_filters_counted_once(self) -> None:
        """Closed purchase + money-market: first filter (purchase) claims it."""
        name_df = make_name_df([("000006", "封闭+货币", "货币型")])
        daily_df = make_daily_df(
            [("000006", "封闭期", "开放赎回")],
            dated=[("000006", "1.0", "1.0")],
        )
        provider = FixtureProvider(name_df=name_df, daily_df=daily_df)
        result = select_candidates(provider, None)
        counts = result.counts.as_dict()
        assert counts["excluded_purchase_status"] == 1
        assert counts["excluded_money_market"] == 0
        assert counts["final_candidates"] == 0


class TestFeeFieldsIgnored:
    def test_missing_fee_column_does_not_break_selection(self) -> None:
        """Fee fields are optional; a daily feed without 手续费 still works."""
        daily_df = make_daily_df(
            [("000001", "开放申购", "开放赎回")],
            dated=[("000001", "1.0", "1.0")],
            include_fee=False,
        )
        name_df = make_name_df([("000001", "X", "混合型-灵活")])
        provider = FixtureProvider(name_df=name_df, daily_df=daily_df)
        result = select_candidates(provider, None)
        assert [c.code for c in result.candidates] == ["000001"]

    def test_unparseable_fee_value_does_not_exclude(self) -> None:
        daily_df = make_daily_df(
            [("000001", "开放申购", "开放赎回")],
            dated=[("000001", "1.0", "1.0")],
        )
        daily_df["手续费"] = ["not-a-fee"]
        name_df = make_name_df([("000001", "X", "混合型-灵活")])
        provider = FixtureProvider(name_df=name_df, daily_df=daily_df)
        result = select_candidates(provider, None)
        assert [c.code for c in result.candidates] == ["000001"]
