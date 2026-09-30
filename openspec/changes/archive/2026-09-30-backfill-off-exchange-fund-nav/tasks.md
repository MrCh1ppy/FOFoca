## 1. Standalone project and storage

- [x] 1.1 Create the Python 3 `fofoca-data` project with pinned/testable AKShare 1.18.97 and PostgreSQL dependencies, CLI entry points, and offline test setup.
- [x] 1.2 Define the three dedicated PostgreSQL tables with the specified keys, column types, NULL/default rules, and timestamps; verify schema creation against a disposable database without accessing Ariadne.
- [x] 1.3 Revalidate per-fund transactional NAV/metadata upserts and atomic SUCCESS state after adding snapshot gap fills: full-history corrections remain authoritative, snapshot-only values fill stored NULLs but never revise stored non-NULL values; test reruns, conflicts, missing fields/dates, and observed first/last dates.
- [x] 1.4 Implement rollback plus separate FAILED attempt/error recording for per-fund failures; test rollback, previous coverage preservation, and failure to record state when DB is unavailable.

## 2. Selection and provider normalization

- [x] 2.1 Recheck reachable `fund_name_em` and `fund_open_fund_daily_em` shapes, code/type/status and dynamically dated NAV columns; implement six-digit normalization, exact open-status and known non-money-market type filtering, with no ETF/LOF name or index-type exclusion and no spot feeds. Treat fee fields as optional and irrelevant to eligibility.
- [x] 2.2 Revalidate shared supplied-code and all-funds selection with the name∩daily rule, fail-closed feed/schema errors, deterministic status/type counts and explicit exclusions; test missing/ambiguous required rows, dual-open LOF `166009`, dual-open ETF samples, varying/missing fee fields and unavailable feeds without claiming Alipay or trading-venue proof.
- [x] 2.3 Revalidate both full-history `fund_open_fund_info_em` indicator fetches and add snapshot parsing of at most the two latest actual column dates, missing date/field supplementation, historical precedence and discrepancy reporting; test invalid values/dates, rounding/overflow, empty/partial history and overlap with offline fixtures.

## 3. Run and query interfaces

- [x] 3.1 Revalidate `backfill` CLI in both modes with identical name∩daily eligibility; process sequentially, report per-fund outcomes including partial historical coverage, snapshot fills and dated/metric conflicts, and exit nonzero on selection or any eligible-fund failure; test continuation and reruns.
- [x] 3.2 Implement the read-only CLI code/inclusive-date query with ascending ISO dates, nullable decimal-string NAV fields, empty unknown-code results and invalid-input rejection; test against stored rows.
- [x] 3.3 Rerun offline tests and a disposable-DB integration test for revised selection (including `166009` and dual-open ETF cases), snapshot supplementation/precedence and no fee storage; run a small live AKShare name/daily/history sample to verify statuses, dated columns, provider limits, duration, memory use and numeric conversion before any broad backfill.

## 4. Authorized implementation-stage delivery

- [x] 4.1 Update usage/operational documentation for the name∩daily dual-open NAV-subscription proxy (including possible ETF/LOF inclusion, no Alipay/trading-venue guarantee and no v0.1 fee storage), dated snapshot gap fills, historical precedence/conflict reporting, independent DB credentials/least privilege, manual backfill/read-only query, precision limits and absence of daily scheduling.
- [x] 4.2 Prepare and verify an Aliyun deployment script for the independent DB/application with secure credential injection and rollback guidance; do not install a timer or modify Ariadne resources.
- [x] 4.3 During `/opsx-apply` only, provision and deploy the already authorized independent Aliyun DB/application once privileges and capacity are checked, and commit/push the authorized new Git repository; verify deployment without enabling automatic daily runs.
