"""Thin wrapper around AKShare 1.18.97.

All upstream access goes through this module so tests can substitute
deterministic fixtures. Functions return raw ``pandas.DataFrame`` objects
exactly as AKShare yields them; normalization lives elsewhere.

Rate limiting is a simple, sequential sleep between provider calls. We do not
parallelize requests because (a) EastMoney throttles aggressively, and (b) the
target host has limited RAM. See ``README.md``.
"""

from __future__ import annotations

import time
from typing import Protocol

import pandas as pd


class Provider(Protocol):
    """Protocol implemented by the live AKShare-backed provider and by fixtures."""

    def fund_name_em(self) -> pd.DataFrame: ...

    def fund_open_fund_daily_em(self) -> pd.DataFrame: ...

    def fund_open_fund_info_em(self, symbol: str, indicator: str) -> pd.DataFrame: ...


class AkshareProvider:
    """Live provider backed by ``akshare`` with a fixed inter-request delay."""

    def __init__(self, request_delay_seconds: float = 1.0) -> None:
        if request_delay_seconds < 0:
            raise ValueError("request_delay_seconds must be >= 0")
        self._delay = float(request_delay_seconds)
        import akshare as ak  # imported lazily so tests need not import akshare

        self._ak = ak

    def _sleep(self) -> None:
        if self._delay > 0:
            time.sleep(self._delay)

    def fund_name_em(self) -> pd.DataFrame:
        self._sleep()
        return self._ak.fund_name_em()

    def fund_open_fund_daily_em(self) -> pd.DataFrame:
        self._sleep()
        return self._ak.fund_open_fund_daily_em()

    def fund_open_fund_info_em(self, symbol: str, indicator: str) -> pd.DataFrame:
        self._sleep()
        return self._ak.fund_open_fund_info_em(symbol=symbol, indicator=indicator)


__all__ = ["Provider", "AkshareProvider"]
