## ADDED Requirements

### Requirement: Current NAV-subscription candidate selection
The system SHALL build the all-funds candidate set from the intersection of `fund_name_em` (code, name, type) and `fund_open_fund_daily_em` (code, purchase/redemption status and recent NAV snapshot). A candidate MUST have purchase status exactly `开放申购`, redemption status exactly `开放赎回`, and a known non-money-market type from the name directory. The system SHALL NOT exclude a code solely because its name contains ETF or LOF, or because its type is `指数型-股票`. This is a pragmatic current proxy for NAV-based subscription/redemption on this platform; even a dual-open ETF may qualify. Neither the daily feed nor this selection proves pure off-exchange trading, historical eligibility, Alipay availability, or strict T+1 confirmation. The system SHALL NOT require `fund_purchase_em`, `fund_etf_spot_em`, or `fund_lof_spot_em`.

#### Scenario: Dual-open LOF and ETF names
- **WHEN** both feeds include `166009` with a known non-money-market type and both statuses exactly open, and another ETF-labelled share also satisfies these conditions
- **THEN** both qualify regardless of their names, without claiming confirmed off-exchange trading or Alipay availability

#### Scenario: Missing or disallowed classification
- **WHEN** a code is absent from either required feed, has a missing/ambiguous type, is money-market, or has a transaction status other than the exact required open value
- **THEN** the system excludes that code from the candidate set

### Requirement: Explicit codes follow the same eligibility policy
The system SHALL validate supplied six-digit codes and SHALL process only supplied codes that pass the same current candidate check used for all-funds selection. It SHALL report ineligible supplied codes without downloading their NAV; this mode MUST NOT bypass eligibility.

#### Scenario: No codes defaults to all candidates
- **WHEN** `backfill` is invoked without any `--code` arguments
- **THEN** the system selects all current candidates without requiring an `--all` flag

#### Scenario: Mixed explicit codes
- **WHEN** `backfill` is invoked with `--code` for one eligible code and one absent or ineligible code
- **THEN** only the eligible code is selected for NAV retrieval and the excluded supplied code is reported

### Requirement: Required feeds fail closed
The system MUST require complete successful, parseable name and daily feed responses with interpretable required code, type, purchase and redemption status fields before selecting any fund. A request failure or unparseable required schema SHALL fail the run without backfilling from a partial candidate set in either selection mode. A missing/ambiguous required value on an individual row SHALL exclude that row, not make it eligible by default; a name string is not an eligibility filter.

#### Scenario: Daily feed unavailable during all-funds selection
- **WHEN** the name feed succeeds but the daily feed request fails
- **THEN** the run exits as failed and does not start NAV requests for any fund

#### Scenario: Name feed unparseable during explicit-code selection
- **WHEN** a code is supplied but required name feed fields cannot be interpreted
- **THEN** the run exits as failed without treating the code as eligible

### Requirement: Auditable selection counts
The system SHALL report name and daily feed sizes, intersection size, counts removed by each purchase-status, redemption-status and type filter in a documented fixed order, final candidate count, and ineligible supplied-code count. Counts SHALL distinguish overlap by assigning each rejected intersected code to the first filter it fails; there SHALL be no ETF/LOF name exclusion count.

#### Scenario: Code fails more than one filter
- **WHEN** a code has closed purchase status and money-market type
- **THEN** the report counts it once in the first failed filter and the final candidate count excludes it

### Requirement: Fee fields do not determine eligibility
The system MAY read fee fields in `fund_open_fund_daily_em`, but fee values SHALL NOT affect candidate selection or be persisted in the v0.1 `fund`, `fund_nav_daily`, or `fund_sync_state` tables. A channel- and time-specific fee snapshot MAY be designed later if needed.

#### Scenario: Optional fee data
- **WHEN** two otherwise eligible funds have different, missing, or unparseable fee values in the daily feed
- **THEN** both remain eligible and neither fee value is persisted in the v0.1 three-table schema
