# fofoca-data v0.1 — Requirements for Review

> **Historical draft — superseded.** This document is retained for history only. The
> authoritative requirements are now the OpenSpec change
> `openspec/changes/backfill-off-exchange-fund-nav/` (see its proposal, design, specs,
> and tasks). The daily timer described here and the ETF/LOF spot-filtering rule no
> longer apply. Do not treat this draft as a second source of truth.

**Status:** Review draft, not an approved implementation specification. Decisions labeled **To confirm** must be agreed before implementation or production deployment.

## Confirmed requirements

- Build a Python 3 data collector named `fofoca-data` for basic information and historical **unit NAV** and **accumulated NAV** of off-exchange, non-money-market funds.
- With fund codes supplied, process those funds individually. Without codes, obtain the catalog from AKShare `fund_name_em()`, exclude money-market and exchange-traded funds, and process the remaining funds individually. The precise catalog classification rule and behavior for unknown codes are **not yet agreed**.
- Persist to a separate database, not Ariadne's database. The requested logical tables are:
  - `fund`: `id bigint` primary key, `code varchar(16)` unique, `name varchar(128)`, `fund_type varchar(64)`, `status varchar(16)` default `ACTIVE`, nullable `established_date`, and timestamps.
  - `fund_nav_daily`: `fund_id` plus `nav_date` as composite primary key, nullable `unit_nav numeric(18,6)`, nullable `accumulated_nav numeric(18,6)`, and timestamps.
  - `fund_sync_state`: `fund_id` plus `dataset` as composite primary key, `first_data_date`, `last_data_date`, `last_sync_at`, `last_sync_status`, `last_error`, and timestamps; the initial dataset is `NAV_DAILY`.
- Historical writes must be idempotent UPSERTs. Support queries by fund code and date range. Automatically fetch new data daily. The meaning of “new,” handling of revised old values, and scheduling details remain **To confirm** below.
- The user has authorized future commits and pushes to `git@github.com:MrCh1ppy/FOFoca.git`, creation of a separate database, and deployment on the Aliyun host via SSH with a systemd timer. **None of those actions are performed during this documentation-only stage.**
- Host information supplied for planning: Python 3.12.12, `uv`, PostgreSQL active, approximately 1.6 GiB RAM with approximately 374 MiB available. Capacity and credentials have not been verified here.

## AKShare capability constraint

For the specified AKShare 1.18.97 interface, `fund_open_fund_info_em(symbol, indicator="单位净值走势")` and the same call with `indicator="累计净值走势"` have **no upstream start/end date parameters**. Each indicator returns the available history of that fund. Consequently, date-scoped rows and date-range queries in our database **do not imply incremental upstream requests**: a daily fetch using this interface re-requests the available full history per selected fund. Do not claim that it fetches only dates since the last sync.

AKShare 1.18.97 applies `pd.to_numeric` to the NAV column, yielding floating-point values. The upstream original decimal precision cannot be recovered from this API's returned values; storing them in `numeric(18,6)` does not restore it.

`fund_etf_fund_info_em(fund, start_date, end_date)` accepts a genuine range, but documents an exchange-traded fund interface; it must not be assumed to work for the off-exchange scope here. `fund_open_fund_daily_em` has no date parameters and supplies a recent two-day snapshot; it is **not** a historical backfill source. Provider response shape, coverage, changes, and operational limits should be validated with representative fixtures and a small live sample before implementation decisions are finalized.

## Proposed approach (not yet approved)

1. Normalize a selected fund's two full-history indicator responses into records keyed by `(fund_id, nav_date)`, retaining the two NAV columns separately. The provider has already converted NAV values to floats; do not claim to preserve upstream original decimal precision. Agree on how values that exceed the six fractional digits of `numeric(18,6)` are rounded or rejected before storage. Do not invent a value where an indicator lacks a date. UPSERT on the composite key, updating values only according to an agreed missing-value/revision policy; never delete rows solely because a provider response omits them.
2. Process funds one at a time, committing in bounded batches/transactions so an interrupted run does not have to discard successfully processed funds. Record per-fund `NAV_DAILY` sync outcomes without treating `last_data_date` as proof of contiguous coverage or an upstream cursor. Record actual observed dates rather than a calculated calendar interval. Keep enough error context for diagnosis without secrets.
3. Offer an explicit-code run for backfills and a separately selectable catalog-driven run. An unattended daily run should be limited to a reviewed selection policy; a full catalog refresh and full-history retrieval across thousands of funds may exceed the host's available memory, provider tolerance, or timer window. Stream or bound processing instead of accumulating all funds' histories in memory.
4. Query only the dedicated database by code and inclusive date bounds, returning stored dates and nullable NAVs without suggesting the database includes every trading day. The query interface (CLI, library, HTTP, etc.) and output shape have not been chosen.
5. Deploy only after review: create a least-privilege dedicated database/user if permissions allow, inject credentials securely, run an unprivileged systemd service/timer with overlap protection, and monitor failures. Production SSH, database administration, systemd changes, and Git operations are outside this documentation step.

## To confirm before implementation

| Decision | Suggested default for review, **not a committed rule** |
| --- | --- |
| Which funds are eligible from `fund_name_em()`? What about missing/ambiguous fund types or explicit codes outside the filtered catalog? | Agree on an allowlist/denylist against actual catalog type values with example codes; skip ambiguous cases with a report rather than silently importing them. Specify whether explicit codes bypass filtering. |
| How is a “new fund” identified and does the unattended daily job discover it and backfill its entire available history? | Separate an explicitly approved catalog discovery/backfill job from daily sync of already-known funds until catalog volume and cost are measured. If automatic discovery is required, specify its cadence, queueing, and backfill limits. |
| Which funds run daily, at what time zone/time, with what frequency, timeout, retry/backoff, and overlap behavior? | Consider one Asia/Shanghai run after NAV publication, with non-overlap and bounded retries; confirm the publication window and capacity first. |
| Should each run UPSERT revised historical NAVs, update only missing/new dates, or periodically reconcile old records? | Prefer explicit reconciliation of provider corrections, subject to confirming how absent/blank values must be treated; the upstream fetch is full-history either way. |
| How are AKShare float NAV values converted for `numeric(18,6)`, including values with more than six fractional digits? | Agree on a rounding or rejection policy and test it against representative provider values; neither choice can recover upstream original decimal precision. |
| What qualifies as success, empty result, partial indicator result, invalid date/decimal, or failure? What do `last_sync_at`, `last_sync_status`, `last_error`, and first/last dates represent? | Treat fetch/parse/write errors as failures, distinguish a valid empty response from an error, and do not advance observed coverage for an empty/failed run. Decide whether partial success is allowed and whether timestamps mark attempt or success. |
| What does `fund.status` mean, and when does it move away from the default `ACTIVE`? | Keep `ACTIVE` as a storage default only; define transitions (e.g. discontinued, temporarily unavailable) separately from sync failures before using status to exclude a fund. |
| Database name, owning role, grants, and permission to create the DB/user on Aliyun? | Request a dedicated DB and least-privilege role; obtain explicit name, credential-handling method, and operator authorization rather than assuming PostgreSQL admin access. |
| Query interface, inputs/outputs, and numeric/date representation? | Agree on a minimal read-only interface with code and inclusive date range; preserve decimal strings and nullable fields. |
| Data retention, provider rate limits/terms, and acceptable initial backfill duration? | Trial a bounded representative subset and measure provider calls, memory, runtime, and DB growth before approving a catalog-wide run. |

## Proposed acceptance criteria (subject to the decisions above)

- Offline fixtures verify explicit-code selection and catalog-driven exclusion using approved examples, including ambiguous/unknown types; no test depends on live AKShare uptime.
- Two NAV indicators with overlapping and disjoint dates merge by fund/date; float-to-`numeric(18,6)` conversion follows the approved rounding or rejection policy without claiming recovery of upstream original precision; missing indicator values remain NULL under the approved policy.
- Re-running the same historical input does not create duplicate funds or NAV rows. Tests cover whether changed historical values update rows under the approved revision policy, and ensure an absent date in a later response does not silently delete one.
- A query for a known code and date range returns only stored in-range dates in the agreed order and representation; empty ranges and unknown codes have documented behavior.
- One fund's provider/parse/DB failure does not masquerade as a successful sync, and any completed other-fund work follows the approved transaction and retry policy. Empty and partial responses follow the agreed sync-state semantics.
- A measured small-scale live trial confirms actual AKShare fields/coverage and timing. Capacity and timer overlap are evaluated before any catalog-wide daily job or production deployment.
- Operational checks verify the eventual database, service, and timer are isolated from Ariadne; no production actions occur during this requirements review.

## Exclusions and safety boundary

No money-market or exchange-traded fund collection; no claims of upstream date-range fetches, historical backfill from the two-day snapshot API, or complete trading-calendar coverage. No inferred fund-status lifecycle, performance guarantees, authenticated public API, UI, or data redistribution contract. **Do not access, migrate, write to, or reconfigure Ariadne tables, database, services, or deployments.** This draft contains no implementation, configuration, credentials, SQL migration, commit, or deployment action.
