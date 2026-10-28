# fofoca-engine (Rust daily-session stage)

Independent edition-2024 Rust library with a **PyO3 extension and thin typed Python
facade** as the Python entry point. No binary, CLI, async runtime, HTTP, SQLite, persistence, data fetching,
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
This helper is **not exposed through Python**. Rust `Session::advance_day` uses it
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
For the Python initialization-only surface, status is `running`, current_date is start_date, last_closed_date is
None, snapshot_pinned is False, and orders is empty. The nested `Account` has
`valuation_date` (start_date), `cash`, `holdings`, `pending_buys`, `pending_cash`,
`confirmed_value`, `pending_buy_value`, `pending_cash_value`, `total_assets`.
All lists are empty, component values are `0.000000000000`, and total_assets equals
cash. All dates are `datetime.date`, all money values are fixed12 strings.
Modifying any returned object cannot change the native session.

Wrong input types raise TypeError; date ranges, decimal grammar/scale raise
ValueError; coefficient/arithmetic overflow raises OverflowError. Native
`NotInitializedError` and `AlreadyInitializedError` both derive from RuntimeError.
Before initialization account/order/day/report calls raise NotInitializedError.
After initialization, account succeeds but `set_orders`, `advance_day`, and `report`
still raise NotImplementedError, never return fake success. Python nested order/market
validation and result conversion, records, and JSON report generation are not
implemented. Rust daily settlement, arrivals, and pinning are not reachable from
Python; its business methods remain explicitly unimplemented.

The thin wrapper exports Snapshot/Account/Holding/PendingBuy/PendingCash TypedDicts,
Order/Market shapes, exceptions, a private native stub, and `py.typed`. It retains
only the native handle: no Python ledger, JSON round trip, subprocess, or Tokio.

```python
from datetime import date
from fofoca_engine import BacktestSession

session = BacktestSession()
session.initialize(date(2026, 1, 1), date(2026, 1, 3), "100")
snapshot = session.account()
assert snapshot["account"]["total_assets"] == "100.000000000000"
snapshot["account"]["cash"] = "0"  # Detached copy, not a transaction.
assert session.account()["account"]["cash"] == "100.000000000000"
```

Rust callers use `Config::parse(NaiveDate, NaiveDate, &str)` (or `Config::new`
with `Money`), `Session::default()`, `Session::initialize(config)`, and
`Session::account() -> Result<Snapshot, SessionError>`. Config is validated and
private with getters; session state is private. Owned snapshots may be changed by
the caller without changing the ledger. The public `decimal` and `model` modules
provide bounded financial values and owned confirmed/pending component structures,
but no transaction mutation API. Core types depend on neither PyO3 nor serde/JSON.

## Packaging and real-wheel validation (repository root)

PyO3 is pinned to stable **0.30.1**. CPython **3.12** is the sole currently
supported and validated baseline (`requires-python = ">=3.12,<3.13"`). Maturin
>=1.9.4,<2 handles extension linking; do **not** enable PyO3's deprecated
`extension-module` feature. Build version-specific, non-abi3 wheels; a wheel is
not promised to import under another interpreter version. Build tools and test
installation live in separate temporary venvs, never the system interpreter.
Use fresh directories if these example names are already occupied.

`fofoca-engine/.python-version` (`3.12`) is a hint for version managers only.
Cargo/PyO3 does not read it, so every Python-feature command below sets
`PYO3_PYTHON=python3.12` explicitly.

```sh
ls /tmp/opencode
python3.12 -m venv /tmp/opencode/fofoca-engine-build-312
/tmp/opencode/fofoca-engine-build-312/bin/python -m pip install 'maturin>=1.9.4,<2'
PYO3_PYTHON=python3.12 /tmp/opencode/fofoca-engine-build-312/bin/maturin build --locked --release \
  --manifest-path fofoca-engine/Cargo.toml \
  --interpreter /tmp/opencode/fofoca-engine-build-312/bin/python \
  --out /tmp/opencode/fofoca-engine-wheels-312
python3.12 -m venv /tmp/opencode/fofoca-engine-test-312
/tmp/opencode/fofoca-engine-test-312/bin/python -m pip install --force-reinstall \
  /tmp/opencode/fofoca-engine-wheels-312/*.whl
/tmp/opencode/fofoca-engine-test-312/bin/python -I fofoca-engine/tests/wheel_contract.py
```

The installed-wheel suite checks native import, independent objects, actual
exceptions and type failures, typing artifacts, exact snapshot shape, decimal/date
bounds, invalid-then-valid and repeated initialization, detached nested copies,
and explicitly unimplemented trading/report methods. An audit hook rejects child-process
operations before import and PATH is empty throughout. No test replaces the
extension or engine with mocks. The wheel contains `fofoca_engine._native` and
`python/fofoca_engine/`. Historical note: an earlier cp314 wheel was built and
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

```sh
cargo fmt --check --manifest-path fofoca-engine/Cargo.toml
cargo check --locked --manifest-path fofoca-engine/Cargo.toml
cargo test --locked --manifest-path fofoca-engine/Cargo.toml
cargo clippy --locked --manifest-path fofoca-engine/Cargo.toml --all-targets -- -D warnings
PYO3_PYTHON=python3.12 cargo check --locked --manifest-path fofoca-engine/Cargo.toml --all-features
PYO3_PYTHON=python3.12 cargo test --locked --manifest-path fofoca-engine/Cargo.toml --all-features
PYO3_PYTHON=python3.12 cargo clippy --locked --manifest-path fofoca-engine/Cargo.toml --all-features --all-targets -- -D warnings
```

Historical note: the former JSON Lines CLI and its process tests were removed;
their earlier passing results are not current validation. Python object disposal
discards memory without automatic close, settlement, export, or recovery.

This stage implements Rust natural-day D+1 settlement, arrivals, fixed-market
retries, stale valuation, and terminal pending retention. Python business
bindings, daily records, reports, persistence, and network access remain deferred.
