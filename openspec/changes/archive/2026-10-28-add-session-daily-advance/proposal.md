## Why

The Rust engine currently has a correct stateless single-day settlement helper, but `Session` cannot consume a complete day's orders and NAVs, commit a successful candidate, or retry a rejected day. The next implementation should establish one small, authoritative daily state transition before expanding the Python binding or reporting surface.

## What Changes

- Add a Rust `Session::advance_day(orders, market)` operation whose arguments contain the complete orders and current-day closing NAV input.
- Treat orders as per-call input rather than persistent session commands; no order list survives the call.
- Pin the first valid market snapshot for a date across business-rejection retries and reject conflicting replacements.
- Apply due pending shares and cash on the opening copy for the current calendar date, then invoke stateless settlement and atomically commit only successful candidates.
- Reject an executable non-zero net order when its fund has no current-day closing NAV; do not silently skip that trade. Empty-order days may still advance and process arrivals.
- Return structured `Advanced` or `Rejected` outcomes for normal business results; reserve session errors for lifecycle, input, and market-conflict failures.
- Finish on `end_date` without advancing beyond it, draining terminal pending assets, or liquidating them.

## Capabilities

### New Capabilities

- `session-daily-advance`: Rust-only daily input, arrival, settlement, retry, atomic commit, calendar advancement, and terminal lifecycle behavior.

### Modified Capabilities

None. The existing `add-local-backtest-engine` change records the earlier skeleton, initialization, and stateless-settlement stages; this change isolates the next implementation slice rather than rewriting that historical change.

## Impact

- Affects `fofoca-engine/src/session.rs`, `settlement.rs`, and small Rust domain modules for market input, arrivals, and daily outcomes.
- Extends the Rust library API but does not yet expose order/market/result conversion through PyO3.
- Adds Rust unit tests for multi-day transitions, rejected retries, missing closing NAV, arrivals on no-NAV days, market conflicts, and terminal pending assets.
- Requires no database, network, persistence, CLI, Tokio runtime, production deployment, or changes to existing FOFoca applications.
