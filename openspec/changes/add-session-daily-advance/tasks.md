## 1. Rust domain inputs and outcomes

- [x] 1.1 Define a typed current-day market snapshot with normalized effective equality, missing-key/`None` semantics, and module-local validation errors without introducing serde, Python, or a second state owner.
- [x] 1.2 Define `AdvanceOutcome`, successful-close data, lifecycle errors, market-conflict errors, and structured missing-closing-NAV issues while preserving the existing settlement diagnostics.
- [x] 1.3 Update `settlement::settle` to reject every executable non-zero net lacking a current-day closing NAV, remove missing-NAV trade skips from normal success semantics, and retain zero-net/empty-order behavior.

## 2. Arrival and session state machine

- [x] 2.1 Add a checked working-copy arrival helper that moves due pending buys into confirmed holdings and due pending cash into available cash exactly once, including no-NAV calendar days.
- [x] 2.2 Replace persistent Session order input with `advance_day(orders, market)` and add only the required pinned-market attempt state; preserve private account ownership and detached snapshots.
- [x] 2.3 Validate and pin the first valid current-date market, compare same-day retries effectively, reject conflicts without consuming new orders, and clear the cache only after successful close.
- [x] 2.4 Orchestrate working-copy arrivals, stateless settlement, checked valuation, and atomic candidate commit; rejected business outcomes must retain the committed account/date and pending arrivals.
- [x] 2.5 Commit successful close state, clear ephemeral inputs/cache, set the last-closed date, advance natural calendar dates, and mark `end_date` finished without terminal drain or liquidation.

## 3. Rust behavior tests

- [x] 3.1 Test per-call orders, empty-order advancement, order non-retention, valid market pinning, effective-equal retry, invalid-market non-pinning, and market conflict preservation.
- [x] 3.2 Test missing closing NAV as an atomic whole-batch rejection, zero-net NAV exemption, mixed-fund no-partial-execution, and business rejection retry with repaired orders.
- [x] 3.3 Test due buy/cash arrivals exactly once, rejected working-copy arrival rollback, arrivals on no-NAV days, and the three-day buy/arrival/sell/cash-arrival path without same-day proceeds reuse.
- [x] 3.4 Test nonterminal natural-day advancement, duplicate/finished lifecycle errors, final-day pending assets, year-9999 trade due-date rejection, and account immutability after rejected calls.

## 4. Validation and documentation

- [x] 4.1 Run `cargo fmt --check`, locked default/all-features tests and clippy with `-D warnings`, OpenSpec strict validation, and `git diff --check` for the focused Rust change.
- [x] 4.2 Update `fofoca-engine` Rust API documentation to describe `advance_day(orders, market)`, per-call order lifetime, fixed market retries, missing closing NAV rejection, and the intentionally unimplemented PyO3 business methods.
- [x] 4.3 Record the focused validation evidence and keep Python binding, report, network, persistence, CLI, and production resources unchanged.

## Validation evidence

- `cargo fmt --check --manifest-path fofoca-engine/Cargo.toml`: passed.
- Locked default `cargo check`, `cargo test`, and `cargo clippy --all-targets -- -D warnings`: passed.
- With `PYO3_PYTHON=python3.12`, locked all-features check, test, and clippy (`--all-targets -- -D warnings`): passed.
- Default and all-features tests each passed 23 unit tests and 7 Rust daily-transition integration tests (30 total, no failures); no Python business conversions were added.
- `openspec validate add-session-daily-advance --strict --no-interactive` and `git diff --check`: passed.
- The existing Python adapter only gains an exhaustive-match compatibility fallback for new Rust-only session errors. Its exposed methods, data conversions, and unimplemented business behavior are unchanged. No wheel rebuild or Python business test expansion is claimed.
- Cargo reports a pre-existing user-level `.cargo/config` deprecation warning; no repository configuration was changed.
