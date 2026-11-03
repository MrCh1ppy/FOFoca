"""Thin typed facade over the authoritative Rust-owned session."""
from collections.abc import Mapping
from datetime import date
from typing import Literal, TypedDict

from .. import _native
from .._native import AlreadyInitializedError, FinishedError, MarketConflictError, NotInitializedError

__all__ = [
    "BacktestSession", "NotInitializedError", "AlreadyInitializedError",
    "Order", "Market", "Holding", "PendingBuy", "PendingCash", "Account", "Snapshot",
    "FinishedError", "MarketConflictError", "Execution", "Net", "Skip", "Issue",
    "Advanced", "Rejected", "AdvanceOutcome",
]


class Order(TypedDict):
    code: str
    side: Literal["buy", "sell"]
    shares: str


Market = Mapping[str, str | None]


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
    status: Literal["running", "finished"]
    start_date: date
    end_date: date
    current_date: date
    last_closed_date: date | None
    snapshot_pinned: bool
    orders: list[Order]
    account: Account


class Execution(Order):
    unit_nav: str
    amount: str
    execution_date: date
    due_date: date


class Net(TypedDict):
    code: str
    side: Literal["buy", "sell"] | None
    shares: str


class Skip(Net):
    reason: Literal["missing_nav"]


class MissingClosingNav(TypedDict):
    kind: Literal["missing_closing_nav"]
    code: str


class Oversell(TypedDict):
    kind: Literal["oversell"]
    code: str
    requested: str
    confirmed: str


class InsufficientCash(TypedDict):
    kind: Literal["insufficient_cash"]
    required: str
    available: str


class ArithmeticOverflow(TypedDict):
    kind: Literal["arithmetic_overflow"]
    code: str | None


class DueDateOverflow(TypedDict):
    kind: Literal["due_date_overflow"]


Issue = MissingClosingNav | Oversell | InsufficientCash | ArithmeticOverflow | DueDateOverflow


class Advanced(TypedDict):
    status: Literal["advanced"]
    closed_date: date
    executions: list[Execution]
    skips: list[Skip]
    account: Account
    finished: bool


class Rejected(TypedDict):
    status: Literal["rejected"]
    orders: list[Order]
    nets: list[Net]
    skips: list[Skip]
    issues: list[Issue]


AdvanceOutcome = Advanced | Rejected


class BacktestSession:
    """Own one native session; no Python ledger or subprocess.

    Daily orders and market values are converted by the native adapter. Only
    report generation remains explicitly unimplemented.
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

    def advance_day(self, orders: list[Order], market: Market) -> AdvanceOutcome:
        """Close the current Rust date, or return a repairable business rejection.

        Shape/type errors raise TypeError, invalid values raise ValueError,
        input overflow raises OverflowError. Lifecycle/conflict errors derive
        from RuntimeError. Entire inputs are parsed before changing state.
        """
        return self._session.advance_day(orders, market)

    def report(self) -> None:
        """Reject until implemented; future report export alone will use JSON."""
        self._session.report()
