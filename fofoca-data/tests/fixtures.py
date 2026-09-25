"""Deterministic in-memory provider fixtures for offline tests."""

from __future__ import annotations

import pandas as pd


class FixtureProvider:
    """Provider implementation returning canned DataFrames.

    Each attribute is either a ``pd.DataFrame``, a ``Callable`` producing one,
    or an ``Exception`` instance to raise. This lets tests exercise both
    happy paths and fail-closed error paths without touching the network.
    """

    def __init__(
        self,
        *,
        name_df: pd.DataFrame | Exception | None = None,
        daily_df: pd.DataFrame | Exception | None = None,
        unit_nav: dict[str, pd.DataFrame | Exception] | None = None,
        accumulated_nav: dict[str, pd.DataFrame | Exception] | None = None,
    ) -> None:
        self.name_df = name_df
        self.daily_df = daily_df
        self.unit_nav = unit_nav or {}
        self.accumulated_nav = accumulated_nav or {}

    @staticmethod
    def _resolve(value):
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value()
        return value

    def fund_name_em(self) -> pd.DataFrame:
        return self._resolve(self.name_df)

    def fund_open_fund_daily_em(self) -> pd.DataFrame:
        return self._resolve(self.daily_df)

    def fund_open_fund_info_em(self, symbol: str, indicator: str) -> pd.DataFrame:
        store = self.unit_nav if indicator == "单位净值走势" else self.accumulated_nav
        if symbol not in store:
            raise RuntimeError(f"fixture missing for {symbol}/{indicator}")
        return self._resolve(store[symbol])


# ---------------------------------------------------------------------------
# Convenience builders
# ---------------------------------------------------------------------------


def make_name_df(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    """rows: (code, name, fund_type)"""
    return pd.DataFrame(
        {
            "基金代码": [r[0] for r in rows],
            "拼音缩写": ["XX"] * len(rows),
            "基金简称": [r[1] for r in rows],
            "基金类型": [r[2] for r in rows],
            "拼音全称": ["XX"] * len(rows),
        }
    )


def make_daily_df(
    rows: list[tuple[str, str, str]],
    *,
    dated: list[tuple[str, str, str]] | None = None,
    date1: str = "2026-09-24",
    date2: str = "2026-09-23",
    include_fee: bool = True,
    extra_dated_columns: dict[str, list[str | None]] | None = None,
) -> pd.DataFrame:
    """Build a ``fund_open_fund_daily_em`` fixture.

    ``rows``: list of ``(code, purchase_status, redemption_status)``.
    ``dated``: optional list of ``(code, unit_nav_date1, accumulated_nav_date1)``
               triples. If omitted, no dated NAV columns are added (the
               fixture is then invalid for selection).
    ``date1``/``date2``: actual dates used for the dated columns.
    ``extra_dated_columns``: optional ``{column_name: [values aligned with rows]}``
               for adding malformed/extra dated columns.
    """
    data: dict[str, list] = {
        "基金代码": [r[0] for r in rows],
        "基金简称": ["X"] * len(rows),
    }
    dated = dated or []
    by_code = {d[0]: (d[1], d[2]) for d in dated}
    data[f"{date1}-单位净值"] = [by_code.get(r[0], (None, None))[0] for r in rows]
    data[f"{date1}-累计净值"] = [by_code.get(r[0], (None, None))[1] for r in rows]
    data[f"{date2}-单位净值"] = [None] * len(rows)
    data[f"{date2}-累计净值"] = [None] * len(rows)
    data["日增长值"] = [None] * len(rows)
    data["日增长率"] = [None] * len(rows)
    data["申购状态"] = [r[1] for r in rows]
    data["赎回状态"] = [r[2] for r in rows]
    if include_fee:
        data["手续费"] = ["0.15%"] * len(rows)
    if extra_dated_columns:
        for col, values in extra_dated_columns.items():
            data[col] = list(values)
    return pd.DataFrame(data)


def make_unit_nav_df(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "净值日期": [r[0] for r in rows],
            "单位净值": [r[1] for r in rows],
            "日增长率": [0.0] * len(rows),
        }
    )


def make_accumulated_nav_df(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "净值日期": [r[0] for r in rows],
            "累计净值": [r[1] for r in rows],
        }
    )
