## Why

We need a dedicated historical NAV store for current non-money-market funds whose daily feed reports open NAV-based purchase and redemption, independent of Ariadne. The earlier review draft included daily synchronization, but v0.1 is limited to an explicit historical backfill with inspectable eligibility and failure reporting.

## What Changes

- Introduce a Python 3 `fofoca-data` backfill that selects supplied codes or current candidates from the intersection of reachable `fund_name_em` and `fund_open_fund_daily_em` feeds. Require purchase and redemption statuses to be exactly open and a known non-money-market type; do not exclude a fund because its name contains ETF or LOF. For example, an open-on-both-sides LOF such as `166009` can qualify. This is a pragmatic proxy for the platform's NAV-based subscription/redemption, not proof of off-exchange trading or Alipay availability; ETFs with both statuses open may also qualify.
- Fetch both full-history `fund_open_fund_info_em` NAV indicators per selected fund; supplement only missing dates/fields with at most the two recent dates exposed by the daily snapshot's actual dated NAV columns. Historical values win on overlaps and discrepancies are reported, not silently overwritten.
- Fee fields may be read from the daily feed but SHALL NOT affect eligibility or be stored in the v0.1 three-table schema; a channel- and time-specific fee snapshot is a possible future addition if needed.
- Persist fund metadata, dated NAV rows, and per-fund `NAV_DAILY` backfill outcomes in a separate PostgreSQL database; support safe retries and a read-only code/date-range query.
- Report selection counts and failures; refuse catalog-wide backfill if any required directory fails, and return a failed task status if any selected fund fails while continuing other funds.
- Exclude daily incremental sync, a scheduled job/timer, any promise of Alipay coverage or strict T+1 confirmation, and any use of Ariadne's database or services.

## Capabilities

### New Capabilities

- `fund-eligibility`: Select current conservative candidates and check supplied codes using the same name/daily-snapshot eligibility rule, failing closed on feed errors.
- `historical-nav-backfill`: Retrieve both full-history NAV indicators per fund, supplement gaps from recent daily-snapshot columns, and persist idempotent NAV data and backfill state.
- `stored-nav-query`: Read persisted NAV by fund code and inclusive date range.

### Modified Capabilities

None.

## Impact

New `fofoca-data` Python 3 application, AKShare 1.18.97 name/daily/history interfaces, and a dedicated PostgreSQL database on Aliyun. ETF/LOF spot feeds are not required or used: they failed on the development machine and Aliyun. Implementation-stage work includes creating/pushing the authorized new Git repository and deployment scripts for the standalone DB/application, but this proposal stage performs none of those operations. No timer, Ariadne integration, or application code is part of this change's documentation stage.
