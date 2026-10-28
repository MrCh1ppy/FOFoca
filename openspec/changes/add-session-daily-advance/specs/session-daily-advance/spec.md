## ADDED Requirements

### Requirement: Per-call daily advance input
The Rust session SHALL expose one daily transition accepting the complete ordered order list and the current day's market snapshot together. Orders SHALL be ephemeral call input: no order list SHALL remain in Session after the call, whether the result is a successful advance or a normal business rejection. An empty order list SHALL be valid.

#### Scenario: Empty order list closes a no-trade day
- **WHEN** an initialized running session advances with an empty order list and an empty market map
- **THEN** it closes the current natural date, processes due arrivals, preserves existing assets using committed prices, and either advances or finishes without creating an order command state

#### Scenario: Each retry supplies a fresh order list
- **WHEN** a daily advance is rejected for a business issue and the caller invokes the same date again with a repaired order list
- **THEN** only the repaired list is used, the original list is not implicitly retained or appended, and the rejection remains available in the first returned outcome

### Requirement: Valid current-day market snapshot pinning
The session SHALL accept only a fully valid market snapshot for its current date and SHALL pin the first valid effective snapshot before attempting settlement. Effective equality SHALL ignore map order and decimal spelling, and SHALL treat a missing code and an explicit `None` NAV as equivalent missing data. A different effective snapshot on a same-date retry SHALL return a market-conflict lifecycle error without consuming the new orders or changing the committed account.

#### Scenario: Rejected close retries with the same market
- **WHEN** the first valid market snapshot causes a business rejection and a later call supplies an effectively equal snapshot with repaired orders
- **THEN** the retry is accepted against the pinned market and the market cache remains the same until the date closes successfully

#### Scenario: Conflicting retry market is rejected
- **WHEN** a same-date retry supplies a different effective NAV for any code after a market snapshot has been pinned
- **THEN** the call returns market conflict, leaves the committed account and date unchanged, does not consume the retry orders, and retains the original pinned market

#### Scenario: Invalid market does not pin
- **WHEN** a market contains an invalid date/code/NAV or another structural validation error
- **THEN** the call returns an input error and a subsequent valid market can be accepted as the first pinned snapshot

### Requirement: Missing closing NAV rejects executable trades
For each non-zero per-code net derived from the supplied orders, the settlement SHALL require a positive closing NAV for the current date. A missing map key or explicit `None` SHALL produce a structured missing-closing-NAV issue and SHALL reject the entire batch before any executable trade is committed. Zero nets SHALL require no NAV. An empty order list SHALL not be rejected merely because the market is empty.

#### Scenario: Missing NAV rejects the whole batch
- **WHEN** one of multiple non-zero nets has no current-day closing NAV while another net has a valid NAV
- **THEN** the outcome is `Rejected` with the missing code identified, no fund executes partially, and the account/date remain unchanged

#### Scenario: Zero net does not require NAV
- **WHEN** buy and sell orders for one fund net to zero and that fund has no current-day NAV
- **THEN** the zero-net result is accepted as no trade and does not cause a missing-closing-NAV rejection

### Requirement: Working-copy arrivals and atomic commit
Before calculating a day's settlement, the session SHALL apply all pending buys due on or before the current date to confirmed holdings and all pending cash due on or before the current date to available cash in a working account copy. Arrivals SHALL not require current NAV and SHALL occur on every natural date, including dates with no market values. The committed session SHALL change only after all arrival, settlement, valuation, and checked-total calculations succeed.

#### Scenario: Due assets arrive before trading
- **WHEN** a pending buy and pending sale are due on the current date and the caller advances with valid orders and market data
- **THEN** the buy is available as confirmed shares and the sale proceeds are available as cash for the current day's opening checks, with each pending item removed exactly once

#### Scenario: Rejected settlement does not commit arrivals
- **WHEN** due arrivals are applied to a working copy but the day's order batch is rejected
- **THEN** the committed account remains byte-for-value equivalent to its pre-call state, including the still-pending arrivals, and a later successful retry applies them once

### Requirement: Successful advance and terminal lifecycle
On a successful daily close the session SHALL commit the candidate account, clear the pinned market, and set the last-closed date. A non-terminal close SHALL advance to the next natural calendar date. A close on `end_date` SHALL mark the session finished while retaining pending assets due after the run; it SHALL not advance beyond `end_date`, drain pending assets, or liquidate holdings. Further advance calls after finish SHALL return a lifecycle error while account queries remain available.

#### Scenario: Multi-day buy and sell transition
- **WHEN** day one buys shares, day two begins after those shares are due and sells part of them, and day three begins after the sale cash is due
- **THEN** each successful close commits the expected cash/holding/pending partition without duplicate arrivals or same-day reuse of sale proceeds

#### Scenario: No-NAV calendar day still advances
- **WHEN** a running session has no orders and the current calendar day has no NAV entries
- **THEN** the day closes and the session advances naturally while due assets are processed and no trade is created

#### Scenario: Terminal pending assets remain
- **WHEN** the final date successfully executes a trade that creates a pending asset due after `end_date`
- **THEN** the session is finished at `end_date`, the pending asset remains in the account, and no extra close or liquidation occurs

### Requirement: Structured normal outcomes and lifecycle errors
The daily operation SHALL return a structured `Advanced` outcome containing the closed date, executions, skips where applicable, resulting account, and finished status, or a structured `Rejected` outcome containing the original orders, derived nets, missing-NAV details, and settlement issues. Normal strategy-repair conditions SHALL not be represented as session lifecycle exceptions. Lifecycle, input, and market-conflict failures SHALL use module-local Rust errors.

#### Scenario: Business rejection is inspectable without exception control flow
- **WHEN** a valid daily call fails because of insufficient cash, oversell, or missing closing NAV
- **THEN** the call returns `Rejected` with original input and independent structured issues, while the caller can repair orders and retry without catching a lifecycle exception

#### Scenario: Finished session rejects further advance
- **WHEN** the caller invokes daily advance after a terminal successful close
- **THEN** the call returns the finished lifecycle error, does not modify the terminal account, and account querying still returns the committed terminal snapshot
