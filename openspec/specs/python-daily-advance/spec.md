# python-daily-advance Specification

## Purpose
Expose the Rust-owned daily backtest transition to Python through exact input conversion and detached structured results.
## Requirements
### Requirement: Python submits one complete daily command
The Python facade SHALL expose `BacktestSession.advance_day(orders, market)` where `orders` is a complete list of order mappings and `market` maps six-digit fund codes to decimal-string NAVs or `None`. The facade SHALL not retain orders between calls, and the current Rust session date SHALL remain the only date authority.

#### Scenario: Submit a daily order and market
- **WHEN** an initialized session receives a valid order list and a NAV mapping for its current date
- **THEN** it passes converted values to the Rust session and returns a structured daily outcome

#### Scenario: Empty daily inputs
- **WHEN** an initialized session receives an empty order list and an empty market mapping
- **THEN** it can close a no-trade day and does not require Python to submit a date

#### Scenario: Invalid container shape
- **WHEN** orders is not a list or market is not a mapping
- **THEN** the call raises `TypeError` before changing the Rust session

### Requirement: Python conversion preserves exact domain values
The adapter SHALL validate six-digit ASCII fund codes, `buy`/`sell` sides, strictly positive shares, decimal grammar, and NAV scale using the existing Rust domain constructors. It SHALL reject malformed input before partial order or market conversion can affect session state.

#### Scenario: Valid exact order and NAV
- **WHEN** an order contains a valid code, side, and six-decimal-or-less shares string and market contains a six-decimal-or-less positive NAV string
- **THEN** Rust receives exact fixed-scale values without binary floating-point conversion or rounding

#### Scenario: Invalid order or market field
- **WHEN** a code, side, shares, or NAV is invalid, non-string, non-positive where prohibited, or over its supported scale
- **THEN** the call raises a Python input or numeric exception and the session account and date remain unchanged

### Requirement: Outcomes are detached Python mappings
The adapter SHALL convert successful daily results to a mapping with `status="advanced"`, closed date, executions, skips, detached account data, and finished status. It SHALL convert business rejection to a mapping with `status="rejected"`, original orders, derived nets, skips, and structured issues. Nested values SHALL be newly allocated and mutation by Python SHALL not alter Rust state.

#### Scenario: Successful advancement
- **WHEN** Rust returns `Advanced`
- **THEN** Python receives the committed account snapshot and execution data as independent objects

#### Scenario: Repairable business rejection
- **WHEN** Rust returns `Rejected` for insufficient cash, oversell, or missing closing NAV
- **THEN** Python receives a rejection mapping rather than an exception and can submit a new complete order list for the same pinned market

### Requirement: Lifecycle failures map to Python errors
The adapter SHALL raise Python exceptions for uninitialized sessions, finished sessions, wrong-date or conflicting pinned markets, malformed inputs, and numeric overflow. These failures SHALL not silently advance, partially commit, or clear Rust account state.

#### Scenario: Market conflict after rejection
- **WHEN** a daily call is rejected and a retry supplies a market snapshot different from the pinned effective market
- **THEN** the adapter raises a market-conflict error and the caller can retry with the original market and a new order list

#### Scenario: Finished session
- **WHEN** Python calls `advance_day` after the terminal date has been successfully closed
- **THEN** the adapter raises a finished-session error and account querying still returns the terminal snapshot

### Requirement: Python surface removes obsolete order staging
The Python facade SHALL remove `set_orders` and SHALL not expose the old one-argument `advance_day(market)` contract. All order submission SHALL occur through the two-argument daily call.

#### Scenario: Clean retry input
- **WHEN** Python repairs a rejected strategy decision
- **THEN** it submits the repaired complete order list directly in the next `advance_day` call, with no hidden prior order list
