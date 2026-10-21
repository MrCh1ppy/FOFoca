## ADDED Requirements

Adapter-stage note: PyO3/typed Python construction currently creates only an empty Rust Session; initialize rejects and never creates an account. Default Rust library builds need no Python or wire/JSON types. The Python extension uses the synchronous core; there is no binary, CLI, async runtime, or duplicate Python ledger. All business requirements below remain unimplemented. Decimal-string grammars below describe external input/export contracts, not a requirement that the core store JSON or Python objects; future domain types remain strongly typed Rust values.

### Requirement: Single initialized calendar session
The engine SHALL own one in-memory account with inclusive, fixed `start_date` and `end_date`, `end_date >= start_date`, nonnegative initial cash, and empty confirmed/pending holdings. Binding dates SHALL be datetime.date values in years 0001–9999, encoded exactly `YYYY-MM-DD` in report exports; fund codes SHALL be six ASCII digits. Invalid initialization SHALL leave the session uninitialized, and successful initialization SHALL open start_date exactly once. Repeated initialization SHALL be rejected without mutation. Python SHALL own all strategy decisions and data preparation; the engine SHALL neither fetch data nor impose a D-1 information restriction.

#### Scenario: Invalid then valid initialization
- **WHEN** initialize supplies a reversed range or negative initial cash, followed by a valid initialize
- **THEN** the first request fails without creating an account and the second creates the cash-only account on start_date

#### Scenario: Repeated initialization
- **WHEN** initialize is called after a successful initialize
- **THEN** it returns `already_initialized` without resetting balances, dates, or records

### Requirement: Exact decimal arithmetic
The engine SHALL accept decimal inputs only as strings matching `(?:0|[1-9][0-9]*)(?:\.[0-9]+)?`. Order shares and non-None unit NAV SHALL be positive with at most six supplied fractional digits; initial cash SHALL be nonnegative with at most twelve. Numeric objects, signs, exponent notation, leading zeros, nonfinite values, and excess scale including trailing zeros SHALL be rejected. Every parse, normalization, signed net sum, product, subtraction, aggregate sum, valuation, and output conversion SHALL be checked for exact representation within the `rust_decimal` 96-bit coefficient/scale bound and the field scale. Arithmetic requiring rounding or overflowing SHALL reject the relevant operation atomically without panic or truncation. Output shares/NAV SHALL use fixed six places; money SHALL use fixed twelve places. Derived signed net quantities and profit SHALL allow a minus sign; zero SHALL never be negative. Shares times NAV SHALL remain exact at at most twelve places, with no cent rounding.

#### Scenario: Fractional exact payment
- **WHEN** a purchase of `0.123456` shares executes at unit NAV `1.234567`
- **THEN** the cost is exactly `0.152414703552` and no monetary rounding occurs

#### Scenario: Invalid or unrepresentable numbers
- **WHEN** a call contains float `1.2`, string `NaN`, `1e3`, `01`, order `0`, negative NAV, seven-place shares, thirteen-place cash, or an unrepresentable decimal
- **THEN** it fails with a structured numeric error without changing the account or replacing orders

#### Scenario: Aggregate overflow
- **WHEN** individually representable inputs overflow an exact net, aggregate cost, valuation, normalization, or next-opening balance
- **THEN** the operation returns `numeric_overflow` without partial account/date/record changes or automatic rounding

### Requirement: Whole-list intention replacement and per-fund netting
`set_orders` SHALL replace the entire ordered intention list only after all individual inputs validate. Each intention SHALL contain code, side `buy` or `sell`, and positive fractional shares. Empty lists SHALL cancel intentions. The engine SHALL preserve original ordering and duplicates in the original list, derive signed `buy - sell` net shares per fund in ascending code order, and validate only derived executable operations. Zero nets SHALL produce `no_trade` outcomes without execution or need for NAV. Accepting orders SHALL not promise execution.

#### Scenario: Netting does not execute gross orders
- **WHEN** original orders buy 3 shares and sell 6 of one fund with 3 confirmed shares
- **THEN** the engine evaluates a single sell of 3 rather than a gross purchase and sale

#### Scenario: Zero net without market data
- **WHEN** buy `1.25` and sell `1.25` of one fund are submitted without its NAV
- **THEN** close records a zero net with `no_trade`, no cost, no settlement, and no missing-NAV error

#### Scenario: Invalid replacement
- **WHEN** a replacement list contains one invalid side or zero share value
- **THEN** the whole replacement fails and the previous ordered list remains unchanged

### Requirement: Current-day unit NAV snapshot
Only `advance_day` SHALL accept `{date, unit_nav}` market input, where unit_nav maps fund codes to positive decimal strings or None. Missing keys and explicit None SHALL both mean unavailable. Wrong date, invalid code, malformed/nonpositive values, and extra fields SHALL reject input before pinning or mutation. Python dictionaries supply unique keys; no JSON parser SHALL be required. The first valid current-day snapshot SHALL be pinned even if execution subsequently fails. Equality SHALL compare normalized decimal values and treat missing/None entries as equivalent, ignoring map order; a changed effective snapshot SHALL return `market_conflict`. A rejected batch SHALL NOT commit this candidate to the successful-day valuation cache. A successful close SHALL commit all non-None snapshot prices with that day's date. Neither accumulated NAV nor stale NAV SHALL be an execution price.

#### Scenario: Retry with immutable prices
- **WHEN** a valid snapshot is pinned by an overspending close, then a retry changes its non-null price
- **THEN** the retry returns `market_conflict`, preserving account, committed prices, date, intentions, and records

#### Scenario: Invalid market does not pin
- **WHEN** an advance has a future date or nonpositive NAV and a later request supplies valid current-day prices
- **THEN** the first fails without pinning a snapshot and the second can pin and execute normally

#### Scenario: Equivalent snapshot spelling
- **WHEN** a retry changes `1.2` to `1.200000`, reorders map keys, and removes a null entry
- **THEN** it is the same effective snapshot rather than a market conflict

### Requirement: Missing NAV expires trading without blocking time
For each nonzero net without same-day unit NAV, the engine SHALL produce `missing_nav`, skip execution before affordability/oversell checks, and expire the intention on a successful close. It SHALL continue other executable operations, valuation, calendar advancement, and scheduled arrivals. Skips SHALL become recorded only when the entire daily close succeeds. It SHALL never carry skipped intentions to a later day automatically.

#### Scenario: Missing sell bypasses oversell check
- **WHEN** a fund with no confirmed shares has a net sell but its same-day NAV is null or absent
- **THEN** it is skipped rather than rejected for overselling, and a valid close advances the day

#### Scenario: Weekend arrival despite missing NAV
- **WHEN** Friday buys settle on Saturday and Saturday has no NAV for that fund
- **THEN** Saturday opens with confirmed purchased shares, uses Friday's price for valuation, skips new nonzero intentions, and can close successfully

### Requirement: Atomic batch validation and execution
The engine SHALL compare each executable net sell with opening confirmed shares and compare total executable buy cost across all funds once with opening available cash. Same-day sale proceeds SHALL NOT fund purchases. Any invalid net operation, numeric error, valuation failure, or required date overflow SHALL reject the whole close. It SHALL retain account, date, committed price cache, orders, and records as they were at the current day's opening plus any accepted order replacements; already-applied opening arrivals SHALL not be rolled back or repeated. Only a valid candidate market pin is an allowed rejection side effect. Rejections SHALL expose original intentions, derived nets, provisional skips, and all independently discoverable errors, without concealing price/cost information. Computations prevented by overflow SHALL be identified instead of inventing downstream quantities. Python SHALL repair the list via `set_orders` and retry.

#### Scenario: Aggregate overspending and no recycling
- **WHEN** opening cash is 10, two net buys cost 6 each, and a net sale would yield 20
- **THEN** the whole close fails with required buy cash 12 and available cash 10; the sale cannot fund either buy

#### Scenario: Retry preserves opening arrivals
- **WHEN** an arrival has already increased today's cash, close rejects an oversell, and Python replaces the list and retries
- **THEN** the rejection changes neither balances nor date nor committed prices, arrival is not repeated, and the eventual success creates exactly one daily record

### Requirement: Next natural day settlement and terminal boundary
A successful buy SHALL deduct cash immediately and create pending shares due D+1; a successful sell SHALL deduct confirmed shares immediately and create pending cash due D+1. Every pending item SHALL include its exact expected date, code, amount/shares, and execution date. Pending shares SHALL not be sellable and pending cash SHALL not be spendable before arrival. A successful nonterminal close SHALL clear orders and pinned snapshot, advance exactly one natural day, and apply all due arrivals once before returning. Closing end_date SHALL instead mark `finished`, retain current_date=end_date and pending assets, and perform no extra opening, liquidation, or settlement drain. Finished sessions SHALL permit only account/report; stale/duplicate advances SHALL fail. Unrepresentable D+1 for a trade SHALL reject rather than wrap dates.

#### Scenario: Three-day cash and share conservation
- **WHEN** initial cash is 100 over January 1–3, day 1 buys 10 shares at 2, day 2 sells 4 at 3 after the buy arrives, and day 3 has no trade with NAV 3
- **THEN** day 1 closes with cash 80, pending shares 10 worth 20, total 100; day 2 opens with confirmed 10 and closes with cash 80, confirmed 6 worth 18, pending cash 12, total 110; day 3 opens with cash 92 and confirmed 6 and finishes with total 110 and no pending assets

#### Scenario: Last-day pending assets
- **WHEN** a final-day buy or sell executes
- **THEN** the session finishes that date with its D+1 pending shares or cash retained and counted once, and a further advance cannot settle it

#### Scenario: Duplicate close request
- **WHEN** a successful nonterminal advance is followed by another advance bearing the old market date
- **THEN** it fails with a date error without advancing or settling again

### Requirement: Valuation is an exact asset partition
The engine SHALL value confirmed shares and pending buy shares separately at the latest committed successful-day unit NAV, using today's candidate snapshot when calculating a close. It SHALL preserve price_date and stale indicators for each priced component. Total assets SHALL equal available cash + confirmed holding value + pending buy value + pending cash, without counting sold shares or settled items twice. Missing a needed price SHALL produce `valuation_unavailable`, not zero. Only a successful close SHALL update the price cache; rejected prices SHALL not change account/report valuation. Initial empty holdings SHALL need no NAV. Fees and distributions SHALL be zero/unmodeled and results SHALL make no real-world total-return guarantee.

#### Scenario: Stale valuation and pending accounting
- **WHEN** a prior successful price exists, today's NAV is missing, and the account holds confirmed or pending bought shares
- **THEN** those shares use the prior price with its date and stale=true while pending cash is valued at its fixed amount

#### Scenario: No available valuation price
- **WHEN** a candidate account contains shares without any usable committed or candidate unit NAV
- **THEN** close fails with `valuation_unavailable` and leaves the ledger and daily records unchanged
