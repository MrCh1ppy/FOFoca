"""Thin typed facade over the authoritative Rust-owned session."""
from datetime import date
from typing import Literal, TypedDict

from . import _native
from ._native import AlreadyInitializedError, NotInitializedError

__all__ = [
    "BacktestSession", "NotInitializedError", "AlreadyInitializedError",
    "Order", "Market", "Holding", "PendingBuy", "PendingCash", "Account", "Snapshot",
]


class Order(TypedDict):
    code: str
    side: Literal["buy", "sell"]
    shares: str


class Market(TypedDict):
    date: date
    unit_nav: dict[str, str | None]


class Holding(TypedDict):
    code: str
    shares: str
    unit_nav: str
    price_date: date
    stale: bool
    value: str


class PendingBuy(Holding):
    execution_date: date
    due_date: date


class PendingCash(TypedDict):
    code: str
    amount: str
    execution_date: date
    due_date: date


class Account(TypedDict):
    valuation_date: date
    cash: str
    holdings: list[Holding]
    pending_buys: list[PendingBuy]
    pending_cash: list[PendingCash]
    confirmed_value: str
    pending_buy_value: str
    pending_cash_value: str
    total_assets: str


class Snapshot(TypedDict):
    status: Literal["running"]
    start_date: date
    end_date: date
    current_date: date
    last_closed_date: date | None
    snapshot_pinned: bool
    orders: list[Order]
    account: Account


class BacktestSession:
    """Own one native session; no Python ledger or subprocess.

    Initialization and detached cash-only snapshots are implemented. Trading,
    daily settlement, and report generation remain explicitly unimplemented.
    """

    __slots__ = ("_session",)

    def __init__(self) -> None:
        self._session = _native.Session()

    def initialize(self, start_date: date, end_date: date, initial_cash: str) -> None:
        """Initialize once with inclusive dates and nonnegative decimal-string cash."""
        self._session.initialize(start_date, end_date, initial_cash)

    def account(self) -> Snapshot:
        """Return independent nested objects; modifying them cannot change the ledger."""
        return self._session.account()

    def set_orders(self, orders: list[Order]) -> None:
        self._session.set_orders(orders)

    def advance_day(self, market: Market) -> None:
        self._session.advance_day(market)

    def report(self) -> None:
        """Reject until implemented; future report export alone will use JSON."""
        self._session.report()
