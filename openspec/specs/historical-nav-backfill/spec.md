# historical-nav-backfill Specification

## Purpose
TBD - created by archiving change backfill-off-exchange-fund-nav. Update Purpose after archive.
## Requirements
### Requirement: Fetch complete available history per eligible fund
The Python 3 `fofoca-data` backfill SHALL process selected funds individually and request both `fund_open_fund_info_em` indicators (`单位净值走势` and `累计净值走势`) for each without an upstream date range. It SHALL use the selected fund's `fund_open_fund_daily_em` snapshot only to supplement missing history, not as a full-history source, and SHALL NOT use the exchange-traded `fund_etf_fund_info_em` range API. It MUST NOT offer a daily incremental or scheduled/timer sync in v0.1.

#### Scenario: Historical run
- **WHEN** an eligible fund is selected for backfill
- **THEN** the system fetches all history available from both indicators for that fund, regardless of dates already stored

### Requirement: Supplement history using actual dated snapshot columns
The system SHALL discover valid calendar dates from daily-snapshot column names matching `YYYY-MM-DD-单位净值` or `YYYY-MM-DD-累计净值`, use at most the two latest distinct dates present, and map each available positive finite value to the date and NAV field named in its column. It SHALL NOT assign an assumed current date, use column position as a date, invent an absent metric, or treat this recent snapshot as full history. Malformed dated NAV columns or invalid present values for a selected fund SHALL fail that fund rather than silently misdate or discard data.

#### Scenario: Dynamic dates and missing metric
- **WHEN** snapshot columns expose three distinct dated NAVs and one of the two latest dates has only a unit NAV column
- **THEN** only values from the latest two actual dates are considered, and no accumulated NAV is invented on the date without that column

#### Scenario: Invalid dated value
- **WHEN** a selected fund's snapshot has an invalid calendar date in a dated NAV column or a nonpositive present NAV
- **THEN** the fund fails rather than persisting a guessed date or value

### Requirement: Merge and validate dated NAV
The system SHALL outer-join the two full-history indicators on actual valid calendar dates; absent values in a new dated row SHALL remain NULL unless supplied by a valid dated snapshot field. For each date and metric, a non-NULL historical value SHALL take precedence; a snapshot value SHALL fill only a missing historical value/date. The system SHALL report differing non-NULL historical and snapshot values by fund, date and metric without silently overwriting history. It SHALL reject malformed nonempty history responses, non-finite or nonpositive present NAV values, and values exceeding `NUMERIC(18,6)` after conversion. It SHALL convert provider numeric values using their string representation and round to six decimal places with `ROUND_HALF_UP`, without claiming to recover original source precision. A history request failure or two valid empty history indicators SHALL fail that fund even if snapshot values exist; one valid empty history indicator with a nonempty other SHALL record `SUCCESS` while reporting partial historical coverage, not manufactured values.

#### Scenario: Overlapping and disjoint dates
- **WHEN** unit NAV has dates A and B while accumulated NAV has dates B and C, with no snapshot values for these dates
- **THEN** the system stores dates A, B, C with NULL only for the missing indicator on newly created rows A and C

#### Scenario: Snapshot fills a missing historical field and date
- **WHEN** history has only unit NAV on date A and no row on date B while the snapshot has accumulated NAV on A and unit NAV on B
- **THEN** the missing accumulated NAV on A and unit NAV on B are stored using their actual snapshot-column dates, with other absent fields NULL

#### Scenario: Same-date discrepancy
- **WHEN** a snapshot NAV differs from non-NULL full-history NAV for the same date and metric
- **THEN** the historical NAV wins and the run reports the discrepancy with fund, date and metric

#### Scenario: Empty and failed indicator calls
- **WHEN** both history indicators are empty, an indicator request fails, or an indicator response has malformed dates
- **THEN** the fund is marked failed rather than recorded as a complete successful backfill, even if snapshot data exists

#### Scenario: Partial but valid indicator coverage
- **WHEN** one indicator returns a valid empty result and the other returns dated NAV
- **THEN** the system stores the available values and eligible snapshot gap fills, records `SUCCESS` for that fund, and reports partial historical indicator coverage in the run result

#### Scenario: Provider floating-point precision
- **WHEN** the numeric provider value string has more than six fractional digits
- **THEN** the stored decimal is rounded to six places with HALF_UP, not described as the provider's original decimal value

### Requirement: Dedicated schema and idempotent writes
The system SHALL store data only in an independent PostgreSQL database with `fund` (`id BIGSERIAL` primary key, unique `code VARCHAR(16)`, `name VARCHAR(128)`, `fund_type VARCHAR(64)`, `status VARCHAR(16) DEFAULT 'ACTIVE'`, nullable `established_date`, timestamps), `fund_nav_daily` (composite primary key `(fund_id, nav_date)`, nullable `unit_nav` and `accumulated_nav` each `NUMERIC(18,6)`, timestamps), and `fund_sync_state` (composite primary key `(fund_id,dataset)`, nullable `first_data_date`, `last_data_date`, `last_sync_at`, `last_sync_status`, `last_error`, timestamps). `fund_id` SHALL reference `fund.id`; the initial dataset SHALL be `NAV_DAILY`. A rerun SHALL upsert non-NULL full-history NAV values, including corrected historical values, and fill stored NULLs from snapshot-only values, without creating duplicates, deleting absent dates, replacing stored non-NULL NAV with NULL, or letting a snapshot revise stored non-NULL NAV. `fund.status` SHALL NOT change solely due to backfill failure.

#### Scenario: Repeated and corrected history
- **WHEN** a fund is backfilled twice and a previously stored date has a revised non-NULL full-history NAV
- **THEN** there is still one row for that fund/date and its relevant NAV value reflects the historical revision

#### Scenario: Omitted earlier field or date
- **WHEN** a later response omits a stored date or has no accumulated NAV for a date whose accumulated NAV is already non-NULL
- **THEN** the existing date/accumulated NAV remains stored and no non-NULL value is silently replaced by NULL

#### Scenario: Snapshot retry does not revise stored value
- **WHEN** a later snapshot supplies a different NAV for a date/field already stored as non-NULL and no full-history value is available for that field
- **THEN** the stored non-NULL value remains unchanged; only a full-history value can revise it

### Requirement: Fund-level atomicity and truthful outcomes
The system SHALL commit a fund's metadata, NAV upserts, and successful `NAV_DAILY` sync state in one transaction. `first_data_date` and `last_data_date` SHALL reflect minimum and maximum dates actually stored for that fund, not contiguous coverage or an upstream cursor. On fund failure the system SHALL roll back that fund's unfinished data writes and separately record `FAILED`, attempted `last_sync_at` and a sanitized `last_error` when the database is available, without advancing prior first/last dates. On success it SHALL record `SUCCESS`, attempt time and clear the prior error. It SHALL continue other selected funds after a fund-level failure and return a nonzero task exit status if any eligible fund failed; a failed error-state write MUST NOT be reported as persisted success.

#### Scenario: Failure after some funds succeed
- **WHEN** the first fund commits successfully and a later fund fails during fetch, parsing, or database writing
- **THEN** the first fund remains committed, the later fund has no partial successful data transaction, subsequent funds are attempted where possible, and the task exits failed

#### Scenario: Coverage after failed retry
- **WHEN** a fund with existing stored NAV fails on a later attempt
- **THEN** its previous first/last stored dates remain unchanged and its attempt status and sanitized error are recorded if database access permits

