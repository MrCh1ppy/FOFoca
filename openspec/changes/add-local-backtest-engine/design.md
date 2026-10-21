## Context

The existing Python ingestion application and independent read-only Rust API remain unchanged. Python owns all data acquisition/preparation and strategy decisions. Rust owns one authoritative simulated account and calendar. Trusted authors control information use; there is no server-side D-1 visibility rule, strategy metadata requirement, or look-ahead audit.

Phase 1 delivered the library/binding skeleton. A later approved stage added bounded decimal types, minimal account components, typed configuration, successful initialization, and detached account queries. The currently approved stage adds only a Rust stateless single-day settlement calculation over an opening account: typed orders, checked derived nets, missing-NAV skips, structured issues, opening-cash/confirmed-share checks, D+1 pending components, and exact candidate valuation/total partition. `Session` does not commit that candidate. `set_orders`/`advance_day` wiring, price pinning/conflict, next-opening arrivals, daily records, reports, Python business methods, networking, and persistence remain unimplemented; this is not a full apply. The entry point is a PyO3 extension plus thin typed Python wrapper, with no binary, CLI, or async runtime. Other boundaries remain daily Python-fed NAV, in-memory state, fractional-share netting, natural-day settlement, atomic retry, and JSON-only report export.

## Goals / Non-Goals

**Goals:**
- A deterministic local account with exact arithmetic, testable state transitions, informative errors, and no duplicate settlement on retry.
- A thin typed in-process Python API, with no child process, async runtime, JSON protocol, or duplicate Python ledger.
- Auditable daily and terminal assets, including pending and stale-priced assets.

**Non-Goals:**
- Data loaders, networking, HTTP, SQLite, restart/recovery, storage abstractions, concurrent session execution, or workspace refactoring.
- Strategy execution in Rust, information policing, business-day clearing, fees, dividends, distributions, automatic liquidation, HTML, or advanced performance metrics.
- Any production deployment, database access, or modification of existing applications.

## Decisions

### Implementation defaults proposed here

These defaults close implementation-blocking details for review. The capability specs make them testable; changing a default requires synchronizing those specs and tasks.

### 1. Independent Rust library and optional PyO3 binding

Use an independent edition-2024 `fofoca-engine/` crate with Cargo.lock, rlib+cdylib, and default-empty features. Its controlled facade exposes synchronous Session, validated private Config with date/cash getters, and owned strongly typed Snapshot/Account components, independent of Python and wire/JSON. Config::parse validates cash strings and Config::new accepts Money; both validate inclusive dates before Session::initialize(config) commits a cash-only account. Session state is private and account() clones a detached Snapshot, never a mutable ledger reference. A future local Rust application can directly link the library and compose data/report components, without HTTP, SQLite, or Python linkage.

The optional `python` feature enables private python.rs and PyO3 0.30.1 targeting CPython 3.12, the sole currently supported and validated baseline. Maturin >=1.9.4,<2 builds the mixed project using python/fofoca_engine/ and private fofoca_engine._native. Maturin handles extension linking; do not enable deprecated PyO3 extension-module, which can break ordinary cargo test linking. No abi3/free-threaded portability claim is made; the version-specific non-abi3 wheel is not promised to import under other Python versions. Default Rust builds have no PyO3 dependency. The wrapper has typed signatures, TypedDict order/market/account/snapshot shapes, py.typed, and a native stub; it stores only a native session handle. Native mapping matches error variants, never parses Display: incorrect types (including datetime.datetime) -> TypeError; date range/decimal syntax/scale -> ValueError; coefficient/operation overflow -> OverflowError; NotInitialized and AlreadyInitialized -> native RuntimeError subclasses. No JSON serialization, subprocess, or Tokio runtime occurs in binding calls. No adapter error enum or meaningless layer wrapping is needed. Construction makes independent native objects; no shared ledger or Python state machine is added.

`initialize(start_date: datetime.date, end_date: datetime.date, initial_cash: str)` validates then creates an empty account; strings are not accepted as dates, and int/float/Decimal are not accepted as cash. account returns a fresh nested Snapshot TypedDict with dates as datetime.date and money as fixed12 strings. Its exact keys are status/start_date/end_date/current_date/last_closed_date/snapshot_pinned/orders/account. Initially status=running, current_date=start_date, last_closed_date=None, snapshot_pinned=False, orders=[], account has the reporting Account shape, empty component lists, zero component values, total_assets=cash, and valuation_date=start_date. There is no JSON envelope. Orders/market retain reserved typed signatures but nested validation remains future work. Before initialization all business methods reject with NotInitializedError; after initialization account succeeds and set_orders/advance_day/report reject with NotImplementedError. No report success is fabricated.

There is no binary target, cli feature, transport module, or async runtime dependency. SNAFU errors remain module-local: DecimalError, CodeError, OrderError, ConfigError, and SessionError; ConfigError retains its initial-cash source. Python explicitly converts variants to exceptions. Chrono validates NaiveDate years 1..9999; rust_decimal stores checked fixed-scale values. A single Session owns the account and dates; future helpers compute candidates and only Session commits. `settlement` is now a stateless Rust helper that borrows an opening account and returns an owned candidate or structured rejection; it is not yet part of the session lifecycle and commits nothing. No storage trait, transaction mutation API, unified error abstraction, committed order/market/record implementation, or concurrency wrapper is introduced.

The initialization stage implements bounded nonnegative Money/Shares, positive Nav, six-ASCII-digit Code, minimal confirmed/pending component structures, private validated Config, and cash-only account snapshots. Owned snapshots are caller-editable copies, not mutable session references. Pending lists have real typed elements but Session still creates no pending items. There are no fake historical records or price caches. Settlement now has a stateless Rust candidate API but no session commit; report remains a documentation-only boundary. Signed netting exists only for the single-day settlement calculation: signed profit/output and every committed trading/report scenario still require later authorization, so this intermediate build is not compliant with all final scenarios.

Alternative: Python-owned balances plus a stateless Rust calculator cannot enforce authoritative account invariants. Historical note: the earlier subprocess CLI and its tests were removed, not retained as a compatibility obligation. Local extension packaging is accepted, while HTTP and database/storage surfaces remain excluded.

### 2. Exact bounded decimal contract

Binding decimals are strings with grammar `(?:0|[1-9][0-9]*)(?:\.[0-9]+)?`; no sign, whitespace, exponent, commas, leading zeros, NaN, Infinity, or numeric objects. Shares and unit NAV accept at most six fractional digits; orders and non-null NAV must be strictly positive. Initial cash accepts at most twelve fractional digits and may be zero. Internal signed net shares are derived, not accepted as order input. Output uses fixed six places for shares/NAV and twelve for money, including signed profit/net shares where applicable.

Use checked decimal parsing, scale checks, addition/subtraction, products, and totals. Every accepted value and intermediate result must fit `rust_decimal`'s exact representation (96-bit coefficient, scale at most 28) and the field's scale; reject overflow or any operation requiring rounding. Check exactness rather than trusting a library operation that might rescale/round to fit. There is no cent rounding: shares times NAV has at most twelve fractional digits. Even six-place normalization is checked; successful canonical output must never become a later serialization failure. Input scale limits count supplied digits, including trailing zeros. Integer account totals may still overflow; bound checks apply to sums as well as individual fields.

The current minimal implementation stores fixed6/fixed12 Decimal values, using exact u128 coefficient checks before construction, addition, nonnegative subtraction, or shares*Nav products. It never invokes rounding/rescale arithmetic. The fixed-scale money maximum is 79228162514264337.593543950335; shares/Nav maximum is 79228162514264337593543.950335. Product coefficient overflow rejects even if the library could lower scale to approximate it. Internal shares may be zero; positive input parsing is separate. Settlement derives checked signed buy-minus-sell nets with an explicit no-trade value, but no general-purpose numeric framework, signed profit, or signed output serializer is implemented.

Alternative: f64 and tolerance checks make boundary affordability unstable. Arbitrary precision adds a dependency/representation without a current need; bounded decimals with explicit atomic rejection suffice.

### 3. Lifecycle and daily transaction boundary

`initialize` fixes inclusive start/end dates, nonnegative cash, and empty holdings. Invalid initialization leaves the session uninitialized; successful initialization cannot be repeated. Binding dates are datetime.date values in years 0001–9999; report dates are encoded exactly `YYYY-MM-DD`. Codes are exactly six ASCII digits. Initialization opens start_date with no pending arrivals.

`set_orders` replaces the whole ordered list after validating its shape and individual values. `advance_day` supplies only the current day's market map. After a successful nonterminal close, atomically clear orders/snapshot, enter the next natural day, and apply all due arrivals exactly once before answering. The close record describes closing balances before that next opening. If next-opening preparation or any checked arithmetic fails, reject the entire advance and retain the pre-close opening account. No rejected attempt reverses or repeats the arrivals already applied when the current day opened.

Closing end_date sets `finished`, keeps current_date=end_date, clears orders/snapshot, and retains pending assets due on end_date+1. No next opening, drain, or liquidation occurs. Only account/report remain callable. End date 9999-12-31 is allowed at initialize, but a trade requiring an unrepresentable due date rejects atomically; no-trade close remains possible.

### 4. Fixed candidate market versus committed prices

Only `advance_day` accepts market input: `{date, unit_nav: {code: decimal_string_or_none}}`. Reject invalid dates/codes/nonpositive NAV, wrong date, and extra fields before accepting a snapshot. Missing keys and explicit None both mean missing NAV; Python dictionaries already have unique keys, so no JSON parser or duplicate-key transport contract is required. Semantic snapshot comparison ignores map order and decimal spelling and treats missing/None entries identically. The first fully valid current-day market input is pinned, even if the subsequent trade batch fails; a different effective snapshot returns `market_conflict`. This is an explicit market-cache side effect, not a ledger mutation. Invalid market input pins nothing.

On rejection the price cache, balances, date, records, and orders remain unchanged. A pinned candidate does not affect account/report valuation until a close succeeds. On success, commit all non-null snapshot prices dated today to the latest-successful-day cache, including codes without trades. Python may replace orders and retry but cannot revise that day's pinned prices. No future-dated data is silently normalized and no accumulated NAV is accepted.

### 5. Net, skip, validate, execute, value, commit

1. Preserve the original list; derive signed per-code net shares in lexicographic code order using checked arithmetic. Zero nets produce `no_trade` without requiring NAV.
2. For nonzero nets without today's unit NAV, produce `missing_nav` skips before cash/holding checks. Skipped sells cannot cause an oversell error. Outcomes are provisional until the whole close succeeds.
3. Check every executable sell against opening confirmed shares. Compute aggregate executable buy cost and check once against opening available cash, never against sale proceeds. Collect independent errors with original requests and net operations; never shrink or prioritize trades.
4. Build a candidate account: buys deduct cash and create D+1 pending shares; sells deduct confirmed shares and create D+1 pending cash. Check all arithmetic, valuations, and (when applicable) next-opening settlements before committing anything.
5. Value and record the candidate close; commit a single daily row, executed trades/skips, and accepted prices. Failed attempts exist only in responses, not an accumulating error history.

Alternative: sequential order execution would make affordability depend on code ordering and allow same-day recycling. Candidate-state validation is small enough without a transaction/event framework.

The current approved stage implements netting through candidate valuation as `settlement::settle(opening, current_date, unit_nav, orders)`. It returns either an owned `SettlementCandidate` with executions/skips and a new `Account`, or a `SettlementRejection` with the original orders, derived nets, provisional skips, and independently collected issues. The opening account is borrowed and never mutated, so rejection is atomic without rollback. Missing NAV is a skip, not an error; zero nets need no NAV. Executable sells use opening confirmed shares, and one aggregate buy-cost check uses opening cash without sale proceeds. Successful candidates apply only that day's cash/share changes plus D+1 pending items and exact revaluation; due dates outside business years 1..9999 reject. Session commit, candidate-price pinning, next-opening arrivals, daily records, report projections, and Python method wiring remain later stages. Opening account components always carry a stored NAV, so the full contract's missing-pricing `valuation_unavailable` path is not reachable in this model and is not claimed as implemented.

### 6. Valuation without double counting

For each fund, value confirmed shares and pending purchased shares separately at the most recent successful-day unit NAV, using today's accepted candidate prices while evaluating a close. Record the actual price date and `stale = price_date < valuation_date`. Pending sales are cash claims at their fixed execution amount, not shares. Total assets equal available cash + confirmed-share value + pending-buy value + pending cash. Arrival merely moves assets between components.

Never use old NAV for execution. Missing a required valuation price returns `valuation_unavailable` and rejects the close rather than assuming zero. With empty initial holdings and successful buys carrying execution prices, this normally cannot arise, but the guard remains explicit. Account/report queries use only committed prices and cannot change state. Before any close the initial account is fully cash-valued. The final day's new pending buys can use that day's execution NAV. No distributions or fees are modeled; unit-NAV results are not a claim of real-world total return.

### 7. Python binding and explicit report export

Python methods are initialize, account, set_orders, advance_day, and report; construction creates an independent native session and disposal discards memory without implicit settlement/export. Calls use direct Python objects/exceptions, not request/response JSON. Only a future report export uses JSON. The retained local-backtest-protocol capability specifies the binding contract, not a transport. Business rejection diagnostics retain original inputs, derived nets, provisional skips, and independent issues without arbitrary truncation or information masking. This does not introduce a shared error enum or a second ledger.

Version-1 reports contain configuration/assumptions, status and dates, initial/terminal accounts, and successful daily records with originals, net operations, executions, and skips. The report schema is defined in `local-backtest-reporting/spec.md`; it is a read-only projection of core state, not a second ledger. Python must explicitly save a report if needed. Object disposal discards unexported memory without settlement, liquidation, automatic export, or recovery.

## Risks / Trade-offs

- [Bounded decimal capacity] -> Reject unrepresentable input/intermediates without rounding or panics; exercise precision and overflow boundaries in tests.
- [Natural-day, zero-fee, no-distribution model differs from real clearing/returns] -> Export assumptions and weekend examples; make no real-return guarantee.
- [Pinned market after failed close is an intentional side effect] -> Distinguish it from committed valuation; expose snapshot status in account responses and test retries.
- [Trusted Python can use future information or poor data] -> Leave responsibility explicit, retain exact daily evidence, and do not claim a look-ahead-free strategy.
- [In-memory history grows with session length] -> Keep only successful records, no failed-attempt archive; persistence and large-run streaming remain out of scope.
- [End-day pending assets cannot be spent] -> Include them once in terminal total and report their expected dates without draining.

## Migration Plan

No migration or production change is needed. Staged implementation adds only the independent library, mixed Python package, offline fixtures, and local documentation. Validate fmt and locked cargo check/test/clippy for default and all features (python only); explicitly set PYO3_PYTHON=python3.12 for Python-feature commands. Build a real wheel with maturin and install into a separate temporary venv for Python tests. README records exact commands. Build/test tools stay under /tmp/opencode, not system Python. Rollback removes only the new local component; existing applications/data remain untouched. Do not build on the production host.

## Open Questions

No implementation-blocking question remains under the proposed defaults. Review may change decimal capacity/library, command/schema naming, or the terminal policy before implementation, but the defaults above are concrete rather than deferred decisions. HTML, persistence, real-world fee/distribution models, and advanced metrics are future scope, not pending requirements of this change.
