## Why

Python strategies need a local, stateful fund backtesting engine with explicit account invariants and reproducible daily results. The existing ingestion application and read-only HTTP API do not simulate accounts, settlements, or strategy results.

## What Changes

- Add an in-memory Rust library session driven by a trusted Python strategy through a PyO3 extension and thin typed Python wrapper; no binary, CLI, or async runtime.
- Leave all data preparation and strategy decisions to Python; feed each calendar day's unit NAV directly to Rust without network access or a remote-data module.
- Net fractional-share buy/sell intentions per fund, validate the executable batch atomically, and settle purchases and sales on the next natural day without same-day reuse of redemption cash.
- Skip funds missing same-day unit NAV; expire their intentions while continuing settlements and calendar advancement.
- Support replacing rejected intentions and retrying against a fixed daily market snapshot without duplicate arrivals or daily records.
- Export versioned JSON reports with daily assets, original/net intentions, trades, skips, stale valuation dates, and terminal pending assets; do not implement HTML rendering.
- Define minimal reviewable implementation defaults for exact decimal arithmetic, initialization, binding errors, valuation, and terminal behavior in the design and specs.

## Capabilities

### New Capabilities

- `local-backtest-session`: Account lifecycle, daily NAV snapshots, netting, atomic execution, next-day settlement, and valuation.
- `local-backtest-protocol`: Typed in-process Python binding contract, account queries, replacement intentions, retry diagnostics, and lifecycle boundaries. The capability name is retained; it does not require a wire protocol.
- `local-backtest-reporting`: Versioned, read-only JSON results with daily and terminal accounting evidence.

### Modified Capabilities

None. Existing ingestion, eligibility, stored-query, and separate read-only HTTP API contracts remain unchanged.

## Impact

- Adds a separate edition-2024 `fofoca-engine/` library crate with Cargo.lock, synchronous Rust core, optional PyO3 0.30.1 bindings, and maturin mixed packaging; no binary, async runtime, or workspace reorganization. Module-local SNAFU errors preserve source boundaries without a shared error module.
- Requires local fixtures and installed-wheel tests, with no production database, remote services, schema changes, or deployment changes. Extension calls need neither a subprocess nor async runtime. Rust-only users do not enable Python dependencies.
- No HTTP server/client, SQLite, persistence/recovery, dividends, automatic liquidation, or presentation server. A future local Rust composition can link the library directly with data/report components; this change does not implement that application.

## Staged Implementation

Phase 1 delivered the library/binding skeleton. The next approved small stage implements only exact bounded decimal types, minimal account/confirmed/pending structures, validated configuration, successful cash-only Session initialization, detached read-only account queries, and explicit PyO3 output/error mapping. No orders, daily advancement, settlement, priced holdings, historical records, or reports are delivered. This intermediate stage does not claim final capability compliance or authorize a full apply. Later phases require further authorization.

Construction owns an uninitialized native session. `initialize(date, date, str)` validates inclusive dates and nonnegative cash, then opens an empty account once. `account()` returns a detached Snapshot TypedDict with the reporting Account shape, datetime.date values, fixed12 money, and empty orders/asset lists. TypeError/ValueError/OverflowError distinguish input type, validation, and coefficient overflow; native RuntimeError subclasses distinguish uninitialized/repeated initialization. Before initialization all business calls raise NotInitializedError; afterward account succeeds and order/day/report calls still raise NotImplementedError. JSON is only a future report export format, not the call protocol. Historical CLI/process tests remain removed.

A subsequent approved stage adds only a Rust-only stateless settlement calculation. `settlement::settle(opening, current_date, unit_nav, orders)` returns a structured candidate or rejection with checked buy-minus-sell nets, missing-NAV skips, opening-cash/confirmed-share checks, D+1 pending components, exact revaluation, and no opening-account mutation or Session commit. Python `set_orders`/`advance_day`, market pinning, arrivals, records, and reports remain unimplemented. Complete accounting/binding/report tasks stay pending; granular stage evidence is recorded separately.
