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

Only phase 1 is authorized now: library/module skeleton, typed Python binding, outer argument checks, and explicit rejection errors. No successful initialization, trading, precision validation, settlement, valuation, or report is delivered. This skeleton does not claim final capability compliance. Later phases implement the remaining existing tasks only with further authorization; business rules remain unchanged.

The approved phase-1 binding adjustment uses PyO3 plus the typed wrapper without implementing business work or applying the whole change. Python construction owns one empty native session; `initialize(date, date, str)` raises NotImplementedError, and account/order/day/report calls raise the module's NotInitializedError. Only outer types are checked. No configuration, account, or report is fabricated. JSON is only a future report export format, not the in-process call protocol. Historical note: the earlier CLI and its process tests were removed; their results are not current validation. Accounting tasks stay unchecked.
