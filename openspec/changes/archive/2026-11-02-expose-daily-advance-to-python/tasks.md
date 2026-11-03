## 1. Native input conversion and API

- [x] 1.1 Add strict all-or-nothing conversion for Python order mappings into Rust `Order` values.
- [x] 1.2 Convert Python NAV mappings into a `MarketSnapshot` using the current Rust session date and exact decimal constructors.
- [x] 1.3 Replace native `set_orders` and one-argument `advance_day` stubs with mutable two-argument `advance_day(orders, market)` and explicit Python error mappings.

## 2. Detached result conversion and facade

- [x] 2.1 Convert `Advanced` and `Rejected` into newly allocated Python mappings with exact decimal strings, dates, executions, skips, orders, nets, issues, and account snapshots.
- [x] 2.2 Update public TypedDicts and `BacktestSession.advance_day(orders, market)`; remove `set_orders` and obsolete market/date shape.
- [x] 2.3 Regenerate or update `_native.pyi` and documentation to match the public contract and error behavior.

## 3. Python behavior and wheel tests

- [x] 3.1 Update wheel contract checks for argument types, malformed nested values, exception mapping, and no partial state changes.
- [x] 3.2 Add installed-wheel tests for successful multi-day buy/arrival/sell/arrival and empty-market advancement.
- [x] 3.3 Add installed-wheel tests for missing-NAV rejection, repaired-order retry with pinned market, conflict handling, and finished sessions.
- [x] 3.4 Verify returned nested mappings are detached and cannot mutate Rust account state.

## 4. Validation

- [x] 4.1 Run Rust default/all-features tests, formatting, and clippy.
- [x] 4.2 Build and install the CPython 3.12 wheel into an isolated environment and run the complete wheel contract.
- [x] 4.3 Run OpenSpec strict validation and `git diff --check`.
