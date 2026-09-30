# resumable-historical-nav-backfill Specification

## Purpose
TBD - created by archiving change resume-historical-nav-backfill. Update Purpose after archive.
## Requirements
### Requirement: Explicit target-date manual recovery
The `backfill` CLI SHALL accept an optional real `YYYY-MM-DD` `--target-date`; it SHALL NOT infer a target from the current date. After applying the same fail-closed `fund_name_em` ∩ `fund_open_fund_daily_em` dual-open, known non-money-market eligibility rules to all selected codes (including `--code`), it SHALL read each code's stored `MAX(fund_nav_daily.nav_date)` via `fund`. If the maximum is at least the target it SHALL skip the code before either history request, without changing fund or NAV data; a missing fund, no NAV rows, or a maximum below the target SHALL cause both complete history indicators to be fetched. Lookup failures SHALL fail the run rather than be interpreted as an empty result. The lookup SHALL complete its read transaction before network fetching or writing a fund. Without `--target-date`, `backfill` SHALL fetch both full histories for every eligible selected code, even if its stored max is later than the intended recovery target. Neither mode SHALL request history by date, schedule an automatic run, or treat the maximum date as proof of complete/correct historical coverage.

#### Scenario: Eligible code already at target
- **WHEN** an eligible code has a stored maximum NAV date of `2026-09-24` or later and the operator supplies `--target-date 2026-09-24`
- **THEN** the code is reported as skipped before either indicator fetch, the read transaction ends, and the stored rows and metadata do not change

#### Scenario: Missing or older NAV
- **WHEN** an eligible supplied code has no fund row, has a fund row but no NAV rows, or has a maximum NAV date before the supplied target
- **THEN** it is attempted with both full-history requests under the same eligibility checks as an all-codes run

#### Scenario: Ordinary manual recheck and invalid target
- **WHEN** an operator invokes `backfill --code` without a target, or provides an invalid calendar date as the target
- **THEN** the former re-fetches full histories for eligible codes and the latter fails input validation without fetching

#### Scenario: Database lookup fails
- **WHEN** the stored maximum cannot reliably be read
- **THEN** the run fails without calling a missing maximum a skip or starting an unverified fetch

### Requirement: Two-table schema and lossless state removal
The independent PostgreSQL application schema SHALL consist of `fund` and `fund_nav_daily` with their existing identities, keys, column types and data preserved. Bundled schema SQL and runtime `apply_schema`/`init-db` SHALL NOT create or depend on `fund_sync_state`, and repeat initialization SHALL be safe without implicitly dropping existing tables. Migration of an existing deployment SHALL verify the independent DB target, take and verify restorable backup of the old state table and safeguard existing fund/NAV data, then explicitly drop only the old state table without `CASCADE`; if absent the drop step SHALL be a no-op. Backup or verification failures SHALL stop the migration; the two retained tables and their rows SHALL NOT be dropped or rewritten for this migration.

#### Scenario: Existing deployment upgraded
- **WHEN** an operator upgrades a database with all three old tables
- **THEN** backups and counts are checked before an explicit state-table drop, fund/NAV counts and data remain intact, and subsequent schema initialization does not recreate the removed table

#### Scenario: Migration retried
- **WHEN** the old state table is already absent and schema initialization is run again
- **THEN** neither operation fails solely because the table is absent, and only `fund` and `fund_nav_daily` remain

### Requirement: Missing history metrics and atomic NAV writes
For otherwise valid history responses the system SHALL treat actual historical `None`/`NaN` metric cells as missing while validating every row's date and duplicate dates. It SHALL reject malformed dates, duplicate dates, infinite, nonpositive, malformed or `NUMERIC(18,6)`-overflowing present NAVs. If both indicators have no usable historical metrics, the fund SHALL fail even when snapshot NAV exists; if only one has usable history, it SHALL report partial coverage. Dates with neither usable historical metric nor valid dated snapshot fill SHALL NOT be inserted. Valid non-NULL historical NAV SHALL revise stored NAV where necessary; valid dated snapshot NAV SHALL fill missing values only; missing metrics SHALL NOT erase stored non-NULL values. Each fund's metadata and NAV writes SHALL commit atomically, without recording a success state. An attempted fund's failed fetch, validation or write SHALL leave no partial writes for that attempt, SHALL NOT insert a failure-only fund row or failure state, and SHALL be reported through live output and the run report. Failure of one fund SHALL NOT undo previous committed funds; a broken DB connection SHALL stop the run when continuing safely is impossible.

#### Scenario: One metric missing and existing value retained
- **WHEN** history supplies a valid accumulated NAV and a `NaN` unit NAV on a valid date where a non-NULL unit NAV is already stored
- **THEN** accumulated NAV can be upserted and the stored unit NAV remains unchanged

#### Scenario: No usable values on a date
- **WHEN** both historical metrics are missing for a date and no valid dated snapshot value fills either
- **THEN** no all-NULL row is inserted and any existing row remains unchanged

#### Scenario: Invalid present value or empty usable history
- **WHEN** history has an infinite/nonpositive/malformed present value, or both histories contain no usable NAV despite a valid snapshot
- **THEN** the fund fails without partially committing its attempt

#### Scenario: Fund failure after another commits
- **WHEN** one fund has committed and the next fails to fetch or persist its NAV
- **THEN** the earlier fund remains committed, the failed attempt makes no DB failure-state write, and later funds are attempted only when the DB remains usable

### Requirement: Live progress, truthful target reconciliation and bounded fetching
The run SHALL emit flushed, sanitized per-code start, stage, skip, success, timeout and failure information with elapsed time during processing, not only at exit. On normal completion it SHALL produce a structured report with target date (if supplied), selected, attempted, skipped, succeeded and failed counts, and per-fund outcomes. In target mode it SHALL recheck persisted maxima and list all selected eligible codes still below the target (including absent NAV), distinguishing them from failed attempts and skipped codes; it SHALL NOT claim target completion while any remain. A failed attempt or a target-mode shortfall SHALL yield a nonzero exit; reconciliation read errors SHALL fail the run instead of claiming completion. The upstream fetch mechanism SHALL be chosen after inspection of the installed AKShare transport/retry path and tested for both full-history indicators with finite configurable transport bounds where supported. Best-effort timeouts SHALL NOT be called hard wall-clock cancellation, SHALL NOT leave overlapping worker fetches, and SHALL NOT cancel/time-limit SQL fund transactions; a required hard process bound SHALL be external to the application.

#### Scenario: Attempt succeeds but target is not reached
- **WHEN** a successfully committed full-history fetch ends before `2026-09-24` and the run targeted that date
- **THEN** the report counts the attempt as succeeded but lists that code as below target and does not report target completion or exit zero

#### Scenario: Fetch timeout returns control
- **WHEN** a configured upstream fetch timeout actually returns control
- **THEN** the code and indicator stage are logged as a sanitized timeout, no failed state is written, other funds may proceed if safe, and final failure count leads to a nonzero exit

#### Scenario: Provider does not return on a soft deadline
- **WHEN** an upstream call remains blocked despite best-effort timeouts
- **THEN** no overlapping fetch starts, no hard in-process cancellation is claimed, and the manual external process boundary can stop the stalled run without imposing an application timeout on DB writes

### Requirement: Manual shared-host release and recovery
English deployment instructions SHALL require verified backups before dropping the old table, a manually started single-worker run with measured conservative FOFoca-only memory and outer elapsed-time controls, a small `--code --target-date 2026-09-24` sample before any separately authorized all-eligible run, and checks of live report, persisted NAV maxima, global memory/swap/OOM and Ariadne/PostgreSQL health. They SHALL specify stop/diagnose on OOM or sustained stall without auto-restart, no timer and no automatic changes to `vm.swappiness=0` or other services. They SHALL explain that an abrupt termination can omit the final report and that operators must reconcile persisted NAV before retrying; a date threshold is not proof of complete history.

#### Scenario: Safe full-run gate
- **WHEN** a reviewed version is deployed on the shared host
- **THEN** the operator verifies two-table migration and backups, runs and checks a small target-date sample plus resource/service health, and only then explicitly authorizes the unscheduled full run

#### Scenario: OOM or stall recurs
- **WHEN** host OOM evidence or sustained absence of per-fund progress is detected
- **THEN** the operator stops/pauses, inspects service health and persisted dates, and does not auto-restart or infer completion from an absent report

