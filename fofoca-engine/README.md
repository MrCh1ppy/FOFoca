# fofoca-engine (Rust daily-session stage)

Edition-2024 Rust library, now an in-place member of the `fofoca-local` workspace,
with a **PyO3 adapter and thin typed Python facade** as the Python entry point.
The engine itself has no binary, CLI, async runtime, HTTP, SQLite, persistence, data fetching,
environment credentials, deployment, or scheduling. Existing applications are
unchanged. A future local Rust application can directly link this library and
compose data/report components without Python or a subprocess.

## Current scope (not a complete backtest engine)

`BacktestSession()` constructs a distinct uninitialized native session.
`initialize(start_date, end_date, initial_cash)` opens an empty cash-only account
once. Dates must be `datetime.date` (not `datetime.datetime` or strings), in years
1–9999, with start <= end; both endpoints are inclusive, including a one-day
range or 9999-12-31. Initialization does not calculate a future D+1 date.
Invalid attempts create no state; repeated successful initialization cannot reset it.

Cash accepts only strings matching `(?:0|[1-9][0-9]*)(?:\.[0-9]+)?` with at most
12 supplied fractional digits (trailing zeros count). No sign, whitespace,
Unicode digits, leading zeros, exponent, float, int, or Python Decimal is accepted.
Money is nonnegative. Core `Shares` supports zero internal positions and a positive
input parser; `Nav` is positive. Both use at most six fractional digits.
All values must fit a 96-bit coefficient **at their fixed output scale**; the
largest money is `79228162514264337.593543950335`, and the largest shares/NAV is
`79228162514264337593543.950335`. Checked sums, nonnegative subtraction, and
shares * NAV use integer coefficient checks, never implicit decimal rescaling.
For example `0.123456 * 1.234567 = 0.152414703552` exactly. Overflow rejects,
not rounds; money/shares output always has 12/6 places and unsigned zero.

The Rust core also has a stateless single-day settlement helper,
`settlement::settle(opening: &Account, current_date, unit_nav, orders)`, with typed
`Order`/`Side` inputs. It derives checked buy-minus-sell nets by code (zero =
`no_trade`), rejects non-zero nets without today's NAV before sufficiency checks for that code,
checks sells against opening confirmed shares, and checks the aggregate buy cost
once against opening cash without recycling sale proceeds. Any issue returns a
structured rejection containing the original orders, derived nets, provisional
skips, and collected issues while leaving the opening account untouched. A
successful candidate applies only that day's trade changes: buys deduct cash and
create D+1 pending shares, sells deduct confirmed shares and create D+1 pending
cash, and the candidate is revalued exactly with a checked component/total
partition. D+1 outside business years 1–9999 rejects. There is no f64 or rounding.
This helper is not exposed directly through Python. Rust `Session::advance_day` uses it
after working-copy arrivals and commits only a successful candidate.

### Rust daily transition

`Session::advance_day(orders: &[Order], market: &MarketSnapshot)` returns
`Result<AdvanceOutcome, SessionError>`. Orders are per-call borrowed input, never
cached or appended to a later call. Empty orders close a no-trade day.
`MarketSnapshot::new(date, BTreeMap<Code, Option<Nav>>)` validates the year range;
`Code` and positive `Nav` constructors enforce code/price validity. The session
rejects a date other than its current date before pinning. Normalized equality
ignores insertion order, decimal spelling, and explicit `None` entries.

The first valid current-date market is pinned even on business rejection. Retry
with repaired orders and the same effective market; a different market returns
`MarketConflict` without evaluating orders. A successful close clears the pin.
The pin is visible through detached `Snapshot::snapshot_pinned` but is not a
committed valuation. No order queue or `set_orders` exists in the Rust core.

Due pending buys and cash arrive on a disposable opening copy before trading,
including on no-NAV days. Settlement uses only opening cash, never same-day sale
proceeds. A non-zero net missing a closing NAV produces `Issue::MissingClosingNav`
and rejects the entire batch; zero nets require no NAV. Rejections retain original
orders, derived nets, provisional skips, and independent issues. No arrival,
trade, valuation, date, or last-close change commits on rejection.

`AdvanceOutcome::Advanced` contains the closed date, executions, skips, detached
account, and finished flag. Successful closes set `last_closed_date` and advance
one natural day, or mark `Status::Finished` at the inclusive end date without
computing its successor. Terminal pending assets remain; there is no drain or
liquidation. Further advance calls return `SessionError::Finished`; account
queries still work. An executable trade on 9999-12-31 rejects because its D+1
cannot fit the supported years; an empty close can finish that date.

`account() -> Snapshot` returns an independent nested Python dict/list projection,
not a JSON envelope. Its exact top-level keys are `status`, `start_date`, `end_date`,
`current_date`, `last_closed_date`, `snapshot_pinned`, `orders`, `account`.
Status is `running` or `finished`; current_date comes only from Rust,
last_closed_date is None until the first close, and snapshot_pinned reflects a
pending retry. The compatibility `orders` list is always empty; no orders are retained.
The nested `Account` has
`valuation_date`, `cash`, `holdings`, `pending_buys`, `pending_cash`,
`confirmed_value`, `pending_buy_value`, `pending_cash_value`, `total_assets`.
Initially all lists are empty, component values are `0.000000000000`, and total_assets equals
cash. All dates are `datetime.date`, all money values are fixed12 strings;
shares and NAVs are fixed6 strings.
Modifying any returned object cannot change the native session.

### Python daily transition

`advance_day(orders, market) -> Advanced | Rejected` accepts a **list** of order
mappings `{code, side, shares}` and a NAV **mapping** `code -> str | None`.
Codes contain exactly six ASCII digits, sides are exactly `buy` or `sell`, and
shares and NAV strings are strictly positive with at most six supplied fractional
digits, using the decimal grammar above. All order fields are required. Extra
order fields are ignored. The entire order list and market are parsed before
calling Rust; malformed late entries cannot pin the market or change state.
No Python date is submitted: the adapter uses the current Rust session snapshot.
Missing market keys and `None` are equivalent; both mean no current closing NAV.
`advance_day([], {})` closes a no-trade day, including arrivals and stale valuation.
There is no `set_orders`, hidden order queue, or one-argument daily API.

Results are newly allocated dicts/lists at every level:

- `Advanced`: `status="advanced"`, `closed_date`, `executions`, `skips`, `account`,
  `finished`. Executions contain `code`, `side`, `shares`, `unit_nav`, `amount`,
  `execution_date`, `due_date`.
- `Rejected`: `status="rejected"`, original `orders` (canonical decimal strings),
  `nets`, `skips`, `issues`. Nets contain `code`, `side` (`buy`, `sell`, or None
  for zero net), and `shares`. Skips add `reason="missing_nav"` to the same shape.
- Issues use a `kind` discriminator: `missing_closing_nav` adds `code`;
  `oversell` adds `code`, `requested`, `confirmed`; `insufficient_cash` adds
  `required`, `available`; `arithmetic_overflow` adds nullable `code`;
  `due_date_overflow` has no additional fields. Arithmetic/date overflow during
  Rust settlement is a business rejection, not an input exception.

Rejections preserve the account and date but pin the normalized effective market.
Repair orders and retry with that same market; changing the market raises
`MarketConflictError`. An unpriced nonzero order cannot be repaired by adding a
price to the pinned day: remove or net out the order instead.

Wrong container/field types and missing order fields raise `TypeError`; invalid
codes, sides, date ranges, decimal grammar/scale/positivity raise `ValueError`;
input coefficient overflow raises `OverflowError`. Native `NotInitializedError`,
`AlreadyInitializedError`, `FinishedError`, and `MarketConflictError` derive from
`RuntimeError`. Rust market validation errors map to `ValueError` (wrong dates
cannot be supplied through this Python API). Before initialization valid
account/day/report calls raise `NotInitializedError`. After initialization only
`report()` still raises `NotImplementedError`; records/report business remain deferred.

The thin wrapper exports Snapshot/Account/Holding/PendingBuy/PendingCash TypedDicts,
Order/Market and discriminated outcome/issue shapes, exceptions, a private native stub, and `py.typed`. It retains
only the native handle: no Python ledger, JSON round trip, subprocess, or Tokio.

```python
from datetime import date
from fofoca_local.engine import BacktestSession  # fofoca_engine remains a compatibility import

session = BacktestSession()
session.initialize(date(2026, 1, 1), date(2026, 1, 3), "100")
snapshot = session.account()
assert snapshot["account"]["total_assets"] == "100.000000000000"
snapshot["account"]["cash"] = "0"  # Detached copy, not a transaction.
assert session.account()["account"]["cash"] == "100.000000000000"
outcome = session.advance_day(
    [{"code": "000001", "side": "buy", "shares": "1"}],
    {"000001": "1.234567"},
)
assert outcome["status"] == "advanced"
assert outcome["closed_date"] == date(2026, 1, 1)
```

Rust callers use `Config::parse(NaiveDate, NaiveDate, &str)` (or `Config::new`
with `Money`), `Session::default()`, `Session::initialize(config)`, and
`Session::account() -> Result<Snapshot, SessionError>`. Config is validated and
private with getters; session state is private. Owned snapshots may be changed by
the caller without changing the ledger. The public `decimal` and `model` modules
provide bounded financial values and owned confirmed/pending component structures,
but no transaction mutation API. Core types depend on neither PyO3 nor serde/JSON.

## Packaging and real-wheel validation (repository root)

Build/install commands and centralized dependency policy now live in
[`fofoca-local/README.md`](../fofoca-local/README.md). Use only
`fofoca-local/pyproject.toml` and `fofoca-local/Cargo.lock`; the standalone engine
wheel entry point and lockfile have been retired after validating the replacement.
The unified CPython **3.12** wheel exports `fofoca_local.engine` and a compatibility
`fofoca_engine` package, with identical native classes and exceptions. Both imports
use the single `fofoca_local._native` extension; no engine implementation is copied.
The original `tests/wheel_contract.py` remains unchanged and is reused by the local
installed-wheel suite.

The installed-wheel suite checks native import, independent objects, actual
exceptions and type failures, typing artifacts, exact snapshot shape, decimal/date
bounds, invalid-then-valid and repeated initialization, detached nested copies,
four-day buy/arrival/sell/arrival, empty closes, business rejection, repaired retries,
market conflicts, finished sessions, strict nested validation without partial changes,
and the still-unimplemented report method. An audit hook rejects child-process
operations before import and PATH is empty throughout. No test replaces the
extension or engine with mocks. The compatibility `fofoca_engine._native` module
aliases the new native module. Historical note: an earlier cp314 wheel was built and
its eight tests passed on this host; because the wheel is version-specific and
non-abi3, that result is retained only as history and does not promise that the
cp312 wheel imports under 3.14.

## Rust features and validation (repository root)

- Default features are empty: synchronous Rust-only `Session` and module-local
  SNAFU `SessionError`; no dependency on Python, Tokio, or wire/JSON types.
- `python` enables only the private PyO3 adapter. Normal cargo tests link Python
  normally and need its development/link library; maturin supplies extension-specific
  linking for wheel builds. CPython 3.12 is the supported baseline and provides the
  link library used by the Python-feature commands below. Historical (not a current
  support claim): an earlier attempt with this host's system 3.14.7 failed ordinary
  Python-feature cargo test with `unable to find library -lpython3.14`, although its
  wheel built and imported successfully.
- There is no `cli` feature, binary target, JSON Lines transport, or Tokio dependency.
- SNAFU errors belong to their actual modules (`decimal`, `model`, `market`, `session`). The Python
  adapter only converts errors to Python exceptions; it needs no error enum.
- Chrono and rust_decimal back validated dates and exact fixed-scale values.
  The `settlement` module is a stateless Rust candidate helper; Session owns the
  atomic commit and `report` remains documentation-only.

Use the workspace validation commands in the local README. For an engine-only
Rust check use `cargo check --locked --manifest-path fofoca-local/Cargo.toml -p fofoca-engine`;
it still resolves from the authoritative local workspace lockfile.

Historical note: the former JSON Lines CLI and its process tests were removed;
their earlier passing results are not current validation. Python object disposal
discards memory without automatic close, settlement, export, or recovery.

This stage implements Rust natural-day D+1 settlement, arrivals, fixed-market
retries, stale valuation, terminal pending retention, and Python daily bindings.
Daily records, reports, persistence, and network access remain deferred.
