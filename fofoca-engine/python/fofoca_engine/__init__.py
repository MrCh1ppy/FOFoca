"""Thin typed facade. All business operations are deliberately unimplemented."""
from datetime import date
from typing import Literal, TypedDict

from . import _native
from ._native import NotInitializedError

__all__ = ["BacktestSession", "NotInitializedError", "Order", "Market"]


class Order(TypedDict):
    code: str
    side: Literal["buy", "sell"]
    shares: str


class Market(TypedDict):
    date: date
    unit_nav: dict[str, str | None]


class BacktestSession:
    """Own one native session; no Python ledger or subprocess.

    Dates and decimal strings reserve the input API only. No business validation,
    account configuration, or successful report is implemented in this skeleton.
    Float inputs are not supported. Return types will be added with domain types.
    """

    __slots__ = ("_session",)

    def __init__(self) -> None:
        self._session = _native.Session()

    def initialize(self, start_date: date, end_date: date, initial_cash: str) -> None:
        """Always raise NotImplementedError; retain no configuration or state."""
        self._session.initialize(start_date, end_date, initial_cash)

    def account(self) -> None:
        self._session.account()

    def set_orders(self, orders: list[Order]) -> None:
        self._session.set_orders(orders)

    def advance_day(self, market: Market) -> None:
        self._session.advance_day(market)

    def report(self) -> None:
        """Reject until implemented; future report export alone will use JSON."""
        self._session.report()
