## Why

The Rust daily-session state machine is implemented and tested, but the Python entry point cannot yet submit per-day orders and market NAVs or inspect structured daily outcomes. Exposing the already-authoritative Rust operation now lets Python drive the tested engine without duplicating account state or settlement logic.

## What Changes

- Add `BacktestSession.advance_day(orders, market)` as the Python daily transition.
- Convert Python order and market mappings into the existing Rust domain types and convert `Advanced`/`Rejected` outcomes into detached Python mappings.
- Map lifecycle, input, and market-conflict failures to explicit Python exceptions while keeping normal business rejection as a returned result.
- Remove the obsolete Python `set_orders` surface and the old one-argument `advance_day(market)` shape. **BREAKING**
- Add Python 3.12 and installed-wheel coverage for multi-day advancement, retries, missing NAV, and detached results.
- Keep `report()` unimplemented and leave Rust settlement, persistence, networking, and data fetching unchanged.

## Capabilities

### New Capabilities

- `python-daily-advance`: Python access to the Rust-owned daily order, market, settlement, and outcome transition.

### Modified Capabilities

None.

## Impact

- Affects `fofoca-engine/src/python.rs` and `fofoca-engine/python/fofoca_engine/__init__.py`.
- Extends the existing PyO3 exception and type-conversion layer without adding dependencies or a second state owner.
- Updates wheel contract and Python integration tests; Rust domain behavior remains the source of truth.
- Requires rebuilding the CPython 3.12 wheel for validation, but no production resources, database, network, persistence, CLI, or report implementation.
