## Context

`fofoca-engine` currently owns an initialized in-memory `Session` and exposes `settlement::settle` as a pure single-day candidate calculator. The calculator currently treats missing NAV as a normal skip, while the next lifecycle slice requires a complete per-call `orders + market` input and a stricter rule: an executable non-zero order without that day's closing NAV rejects the whole attempt. No Python business binding, report history, persistence, CLI, or network component is needed for this slice.

The design must preserve the existing account invariants: Rust remains the sole mutable state owner, decimal operations remain checked and exact, rejected attempts cannot mutate the committed account/date, and natural calendar days advance even when there are no trades or no NAVs. A rejected attempt may pin a valid market input for same-day retry, but that cache is not a committed valuation.

## Goals / Non-Goals

**Goals:**

- Make `Session::advance_day(orders, market)` the single Rust entry point for one day's complete strategy input.
- Keep orders ephemeral: they are validated and consumed as call input and never remain in Session state.
- Add a minimal typed market snapshot and deterministic effective equality for same-day retry pinning.
- Apply due pending buys and cash to a working opening copy, invoke stateless settlement, and commit only a successful candidate.
- Return structured success or business rejection results while preserving lifecycle/input errors as module-local errors.
- Advance natural calendar dates, support empty-order/no-NAV days, and finish at `end_date` without terminal draining or liquidation.

**Non-Goals:**

- PyO3 conversion of order, market, outcome, or rejection structures.
- Daily records, ReportV1, profit metrics, HTML, persistence, recovery, networking, or concurrency.
- Strategy validation beyond typed order/market domain validation.
- Fees, distributions, business-day calendars, same-day sale proceeds, or automatic liquidation.

## Decisions

### 1. Per-call orders and one daily transition

`Session::advance_day(orders, market)` receives the complete ordered order list and the current day's market input. There is no `set_orders`, `orders` cache, or persistent command queue. This removes hidden stale commands and makes every retry explicitly provide its repaired order list. Empty orders are valid and mean no trading intention for the day.

The Rust result is `Result<AdvanceOutcome, SessionError>`: `Advanced` and `Rejected` are normal domain outcomes; `SessionError` covers uninitialized, finished, malformed/lifecycle, and market-conflict failures. This separates strategy-repair control flow from programming or lifecycle errors.

### 2. Market snapshot pinning

The first fully valid market snapshot accepted for the current date is stored in a `market_attempt` field. Effective comparison is based on normalized `Code -> Option<Nav>` entries, not map insertion order or decimal spelling. Missing keys and explicit `None` represent the same missing NAV. A later same-date call must use an equal effective snapshot; otherwise it returns `MarketConflict` without consuming orders or changing the committed account.

The cache is cleared only after a successful close. A rejected settlement preserves it so Python can submit repaired orders against identical prices. Invalid market input pins nothing. The cache is not used for account valuation until a close commits.

### 3. Working-copy arrivals and atomic commit

`advance_day` clones the committed account into a working opening account, applies all pending buys and pending cash due on or before the current date exactly once, and then calls `settlement::settle` with that working account. The committed Session is untouched while the candidate is being prepared.

On `Rejected`, discard the working account and leave the committed account, date, last-closed marker, and records unchanged; retain only the valid market attempt cache. On `Advanced`, atomically replace the account with the candidate, clear the market cache, set the last-closed date, and either advance to the next natural date or mark the session finished. Arrivals move values between cash/holding/pending partitions and require no current NAV.

### 4. Missing closing NAV is a batch rejection

Before affordability and holding checks, settlement derives per-code nets. For every non-zero net, the market must contain a positive current-day closing NAV. Missing keys and `None` produce a structured `MissingClosingNav` issue. Any such issue rejects the entire batch; the implementation must not execute other funds partially. Zero nets do not require NAV. An empty order list can close a day with an empty market and still process arrivals and existing-asset valuation according to the already stored prices.

### 5. Daily and terminal lifecycle

On a successful non-terminal close, the session advances to the next natural calendar day and the next call begins from that date after arrivals are applied in its working copy. On a successful close of `end_date`, it remains on `end_date`, marks `Finished`, clears the attempt cache, and retains pending assets whose due date is after the run; it does not drain, liquidate, or compute `end_date + 1`. Account querying remains available, while further advance calls return `Finished`.

### 6. Module boundaries

`settlement.rs` remains stateless and only calculates a candidate from an opening account, date, market, and orders. A small arrival helper owns pending-to-confirmed/cash movement. `session.rs` owns orchestration, cache, lifecycle, and commit boundaries. Domain structs for market and outcomes remain strongly typed Rust values. No unified cross-module error enum or storage abstraction is introduced.

## Risks / Trade-offs

- [Pinned market is stateful despite rejected ledger attempts] → Expose pin status through existing snapshots later, test invalid input and conflict behavior, and document that it is not committed valuation.
- [Working-copy arrivals can expose arithmetic failures late] → Perform all arrival, settlement, valuation, and total checks before the single commit; retain the original account on any failure.
- [Missing NAV rejection is stricter than the earlier stateless helper] → Replace the old skip behavior only for this lifecycle contract and add explicit mixed-fund atomicity tests.
- [Per-call orders make retry callers reconstruct their list] → Return original orders in structured rejection results; avoid hidden Session command state.
- [No daily records in this slice limits audit output] → Keep executions, skips, and rejection diagnostics in the immediate outcome; add committed records only in a later reporting stage.

## Migration Plan

This is an offline library change with no database or deployment migration. Add Rust types and tests, update the existing Rust API/tests to call `advance_day(orders, market)`, and leave the current PyO3 methods explicitly unimplemented until a later binding stage. If validation fails, revert the new Rust modules/API; no persisted state or external consumer requires migration.

## Open Questions

None for this implementation slice. Python result conversion, daily-record schema, and report export remain intentionally deferred rather than unresolved.
