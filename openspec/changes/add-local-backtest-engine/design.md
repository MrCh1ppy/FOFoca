## Context

The existing Python ingestion application and independent read-only Rust API remain unchanged. Python owns all data acquisition/preparation and strategy decisions. Rust owns one authoritative simulated account and calendar. Trusted authors control information use; there is no server-side D-1 visibility rule, strategy metadata requirement, or look-ahead audit.

Phase 1 delivers only the crate/adapter skeleton, not the complete engine. The approved adjustment makes a PyO3 extension plus thin typed Python wrapper primary and retains a resident JSON Lines CLI as optional compatibility. Other boundaries remain daily Python-fed NAV, in-memory state, fractional-share netting, natural-day settlement, atomic retry, and JSON-only report export. The business defaults below remain the final implementation target.

## Goals / Non-Goals

**Goals:**
- A deterministic local account with exact arithmetic, testable state transitions, informative errors, and no duplicate settlement on retry.
- A thin typed in-process Python API, with no child process, Tokio runtime, JSON protocol, or duplicate Python ledger; optional CLI compatibility remains separately testable.
- Auditable daily and terminal assets, including pending and stale-priced assets.

**Non-Goals:**
- Data loaders, networking, HTTP, SQLite, restart/recovery, storage abstractions, concurrent session execution, or workspace refactoring.
- Strategy execution in Rust, information policing, business-day clearing, fees, dividends, distributions, automatic liquidation, HTML, or advanced performance metrics.
- Any production deployment, database access, or modification of existing applications.

## Decisions

### Implementation defaults proposed here

These defaults close implementation-blocking details for review. The capability specs make them testable; changing a default requires synchronizing those specs and tasks.

### 1. Independent Rust core, primary PyO3 adapter, and optional thin CLI

Use an independent edition-2024 `fofoca-engine/` crate with Cargo.lock, rlib+cdylib, and default-empty features. Its controlled facade exposes synchronous Session and module-local SNAFU SessionError, independent of Python and wire/JSON. Future domain inputs/outputs stay strongly typed Rust values. The current argument-free initialize placeholder rejects before domain configuration exists. Do not invent an account model in this binding-only phase. A future local Rust application can directly link the library and compose data/report components, without HTTP, SQLite, or Python linkage.

The optional `python` feature enables private python.rs and PyO3 0.30.1 targeting CPython 3.12, the sole currently supported and validated baseline. Maturin >=1.9.4,<2 builds the mixed project using python/fofoca_engine/ and private fofoca_engine._native. Maturin handles extension linking; do not enable deprecated PyO3 extension-module, which can break ordinary cargo test linking. No abi3/free-threaded portability claim is made; the version-specific non-abi3 wheel is not promised to import under other Python versions. Default Rust builds have no PyO3 dependency. The wrapper has typed signatures, TypedDict order/market shapes, py.typed, and a native stub; it stores only a native session handle. Native code checks outer parameter types and converts SessionError directly to NotImplementedError or native NotInitializedError (RuntimeError subclass). No JSON serialization, subprocess, or Tokio runtime occurs in binding calls. No adapter error enum is needed for direct Python conversions. Construction makes independent native objects; no shared ledger or Python state machine is added.

`initialize(start_date: datetime.date, end_date: datetime.date, initial_cash: str)` reserves date/decimal-string inputs but rejects without retaining configuration. Orders use decimal strings and market uses a date plus a string-or-null NAV map. Nested semantics, precision checks, and output domain types remain future business work; float input has no supported precision contract. Business methods all fail while uninitialized, including report; JSON report export will be implemented only with actual report projections.

The optional cli feature enables Tokio, serde/JSON, the narrow run/CliError facade, src/bin/fofoca-engine.rs, and the original process integration test. The binary alone starts Tokio current-thread and handles nonzero failure exits. cli.rs owns generic sequential async BufRead/Write, protocol.rs owns wire schemas/errors, and both adapters use the same session.rs. Tokio keeps only macros/rt/io-std/io-util; no spawn, parallel handling, or Arc/Mutex. Module-local SNAFU CliError/ProtocolError preserve real sources; wire JSON errors are separate response data. Chrono and rust_decimal remain core date/exact-arithmetic dependencies. A single session will ultimately own the ledger, orders/snapshot, price cache, and records; helpers compute candidates and only the session commits. No storage trait is needed.

Phase 1 implements only transport, schema shapes, errors, and quit. Session remains uninitialized: init returns not_implemented; other known business commands return not_initialized with an explicit not-yet-implemented message. All responses have state=null. Decimal, model, settlement, and report modules are documentation-only boundaries, not fake calculations. Date/code/decimal semantics and precision remain deferred, not accepted as business-valid values. No account or report success is fabricated. Remaining tasks require later authorization; this intermediate build is not compliant with all final scenarios.

Alternative: Python-owned balances plus a stateless Rust calculator cannot enforce authoritative account invariants. The earlier subprocess-only decision is superseded by the approved binding adjustment: local extension packaging is now accepted, while HTTP and database/storage surfaces remain excluded. CLI support is compatibility, not a required Python execution path.

### 2. Exact bounded decimal contract

Wire decimals are strings with grammar `(?:0|[1-9][0-9]*)(?:\.[0-9]+)?`; no sign, whitespace, exponent, commas, leading zeros, NaN, Infinity, or JSON numeric literals. Shares and unit NAV accept at most six fractional digits; orders and non-null NAV must be strictly positive. Initial cash accepts at most twelve fractional digits and may be zero. Internal signed net shares are derived, not accepted as order input. Output uses fixed six places for shares/NAV and twelve for money, including signed profit/net shares where applicable.

Use checked decimal parsing, scale checks, addition/subtraction, products, and totals. Every accepted value and intermediate result must fit `rust_decimal`'s exact representation (96-bit coefficient, scale at most 28) and the field's scale; reject overflow or any operation requiring rounding. Check exactness rather than trusting a library operation that might rescale/round to fit. There is no cent rounding: shares times NAV has at most twelve fractional digits. Even six-place normalization is checked; successful canonical output must never become a later serialization failure. Input scale limits count supplied digits, including trailing zeros. Integer account totals may still overflow; bound checks apply to sums as well as individual fields.

Alternative: f64 and tolerance checks make boundary affordability unstable. Arbitrary precision adds a dependency/representation without a current need; bounded decimals with explicit atomic rejection suffice.

### 3. Lifecycle and daily transaction boundary

`init` fixes inclusive start/end dates, nonnegative cash, and empty holdings. Invalid initialization leaves the session uninitialized; successful initialization cannot be repeated. Strict dates are real calendar dates encoded exactly `YYYY-MM-DD` with years 0001–9999; codes are exactly six ASCII digits. Initialization opens start_date with no pending arrivals.

`set_orders` replaces the whole ordered list after validating its shape and individual values. `advance_day` supplies only the current day's market map. After a successful nonterminal close, atomically clear orders/snapshot, enter the next natural day, and apply all due arrivals exactly once before answering. The close record describes closing balances before that next opening. If next-opening preparation or any checked arithmetic fails, reject the entire advance and retain the pre-close opening account. No rejected attempt reverses or repeats the arrivals already applied when the current day opened.

Closing end_date sets `finished`, keeps current_date=end_date, clears orders/snapshot, and retains pending assets due on end_date+1. No next opening, drain, or liquidation occurs. Only account/report/quit remain callable. End date 9999-12-31 is allowed at init, but a trade requiring an unrepresentable due date rejects atomically; no-trade close remains possible.

### 4. Fixed candidate market versus committed prices

Only `advance_day` accepts market input: `{date, unit_nav: {code: decimal_string_or_null}}`. Reject invalid dates/codes/nonpositive NAV, wrong date, extra fields, and duplicate JSON keys at every nesting level before accepting a snapshot. Missing keys and explicit null both mean missing NAV. Semantic snapshot comparison ignores map order and decimal spelling and treats missing/null entries identically. The first fully valid current-day market input is pinned, even if the subsequent trade batch fails; a different effective snapshot returns `market_conflict`. This is an explicit market-cache side effect, not a ledger mutation. Invalid market input pins nothing.

On rejection the price cache, balances, date, records, and orders remain unchanged. A pinned candidate does not affect account/report valuation until a close succeeds. On success, commit all non-null snapshot prices dated today to the latest-successful-day cache, including codes without trades. Python may replace orders and retry but cannot revise that day's pinned prices. No future-dated data is silently normalized and no accumulated NAV is accepted.

### 5. Net, skip, validate, execute, value, commit

1. Preserve the original list; derive signed per-code net shares in lexicographic code order using checked arithmetic. Zero nets produce `no_trade` without requiring NAV.
2. For nonzero nets without today's unit NAV, produce `missing_nav` skips before cash/holding checks. Skipped sells cannot cause an oversell error. Outcomes are provisional until the whole close succeeds.
3. Check every executable sell against opening confirmed shares. Compute aggregate executable buy cost and check once against opening available cash, never against sale proceeds. Collect independent errors with original requests and net operations; never shrink or prioritize trades.
4. Build a candidate account: buys deduct cash and create D+1 pending shares; sells deduct confirmed shares and create D+1 pending cash. Check all arithmetic, valuations, and (when applicable) next-opening settlements before committing anything.
5. Value and record the candidate close; commit a single daily row, executed trades/skips, and accepted prices. Failed attempts exist only in responses, not an accumulating error history.

Alternative: sequential order execution would make affordability depend on code ordering and allow same-day recycling. Candidate-state validation is small enough without a transaction/event framework.

### 6. Valuation without double counting

For each fund, value confirmed shares and pending purchased shares separately at the most recent successful-day unit NAV, using today's accepted candidate prices while evaluating a close. Record the actual price date and `stale = price_date < valuation_date`. Pending sales are cash claims at their fixed execution amount, not shares. Total assets equal available cash + confirmed-share value + pending-buy value + pending cash. Arrival merely moves assets between components.

Never use old NAV for execution. Missing a required valuation price returns `valuation_unavailable` and rejects the close rather than assuming zero. With empty initial holdings and successful buys carrying execution prices, this normally cannot arise, but the guard remains explicit. Account/report queries use only committed prices and cannot change state. Before any close the initial account is fully cash-valued. The final day's new pending buys can use that day's execution NAV. No distributions or fees are modeled; unit-NAV results are not a claim of real-world total return.

### 7. Primary binding, optional explicit protocol, and report

Python methods are initialize, account, set_orders, advance_day, and report; construction replaces CLI process startup and disposal discards memory without implicit settlement/export. Calls use direct Python objects/exceptions, not request/response JSON. Only a future report export uses JSON. The optional CLI commands remain init, account, set_orders, advance_day, report, and quit; exact envelopes and result schemas live in local-backtest-protocol/spec.md and apply to that adapter only. Each input line produces one JSON line and flushes stdout; logs go to stderr. IDs correlate, not persistent idempotency: no registry/server/retry framework. Malformed/unknown lines return structured errors and leave the loop alive. No arbitrary diagnostic truncation or information masking is imposed.

Version-1 reports contain configuration/assumptions, status and dates, initial/terminal accounts, and successful daily records with originals, net operations, executions, and skips. The report schema is defined in `local-backtest-reporting/spec.md`; it is a read-only projection of core state, not a second ledger. `quit` or EOF discards unexported memory and exits successfully without settlement or automatic export. Python must explicitly save a report if needed. EOF with a nonempty final unterminated line processes that line once before exiting; empty EOF produces no response.

## Risks / Trade-offs

- [Bounded decimal capacity] -> Reject unrepresentable input/intermediates without rounding or panics; exercise precision and overflow boundaries in tests.
- [Natural-day, zero-fee, no-distribution model differs from real clearing/returns] -> Export assumptions and weekend examples; make no real-return guarantee.
- [Pinned market after failed close is an intentional side effect] -> Distinguish it from committed valuation; expose snapshot status in account responses and test retries.
- [Trusted Python can use future information or poor data] -> Leave responsibility explicit, retain exact daily evidence, and do not claim a look-ahead-free strategy.
- [In-memory history grows with session length] -> Keep only successful records, no failed-attempt archive; persistence and large-run streaming remain out of scope.
- [End-day pending assets cannot be spent] -> Include them once in terminal total and report their expected dates without draining.

## Migration Plan

No migration or production change is needed. Staged implementation adds only the independent crate, mixed Python package, offline fixtures, and local documentation. Validate fmt and locked cargo check/test/clippy for empty, cli, python, and combined features; build a real wheel with maturin and install into a separate temporary venv for Python smoke tests. README records exact commands. Build/test tools stay under /tmp/opencode, not system Python. Rollback removes only the new local component; existing applications/data remain untouched. Do not build on the production host.

## Open Questions

No implementation-blocking question remains under the proposed defaults. Review may change decimal capacity/library, command/schema naming, or the terminal policy before implementation, but the defaults above are concrete rather than deferred decisions. HTML, persistence, real-world fee/distribution models, and advanced metrics are future scope, not pending requirements of this change.
