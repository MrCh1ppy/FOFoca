# fofoca-data

One-off historical NAV backfill for Chinese mutual funds whose current
`fund_open_fund_daily_em` snapshot reports **open purchase and open
redemption**, from AKShare 1.18.97 into a dedicated PostgreSQL database.

This project is intentionally **standalone**: it does not read from, write to,
or otherwise touch Ariadne's database, services, or deployment. There is **no
timer, no schedule, no daily incremental sync** in v0.1 — only an explicit
operator-run historical backfill plus a read-only query.

## Scope

* Source: AKShare 1.18.97 (`fund_name_em`, `fund_open_fund_daily_em`,
  `fund_open_fund_info_em(indicator="单位净值走势" | "累计净值走势")`).
* Store: a separate PostgreSQL database with three tables (`fund`,
  `fund_nav_daily`, `fund_sync_state`).
* Operations: `init-db`, `backfill`, `query`. Nothing else.

The candidate set is a **pragmatic, current** NAV-subscription/redemption
proxy for this platform. It is **not** proof of off-exchange trading, **not**
an Alipay inventory, and **not** a strict T+1 confirmation. A fund that is
open today and closed tomorrow simply will not be selected tomorrow; the
spec does not try to reconstruct past eligibility.

## What "eligible" means (v0.1)

A fund code is selected **only** if, at selection time, it satisfies **all** of:

1. present in `fund_name_em()` **and** `fund_open_fund_daily_em()`
   (intersection of the two feeds);
2. `申购状态` in the daily feed is exactly `开放申购`;
3. `赎回状态` in the daily feed is exactly `开放赎回`;
4. `基金类型` in the name directory is known (non-blank) and does **not**
   start with `货币型`.

Notes:

* There is **no** ETF/LOF exclusion. An open-on-both-sides LOF such as
  `166009` qualifies; a dual-open ETF may also qualify. We do **not** call
  `fund_etf_spot_em` or `fund_lof_spot_em`, and we do not filter by fund
  name containing "ETF" or "LOF".
* We do **not** call `fund_purchase_em`. Purchase/redemption status comes
  from the daily snapshot only.
* Supplied `--code` values go through the **same** check; explicit codes do
  not bypass eligibility.
* If **either** of the two required feeds fails or is unparseable (missing
  required columns, malformed dated NAV column, invalid calendar date in a
  dated column), the run fails closed before any NAV request is made. A
  single row with a blank type/status is excluded from candidacy without
  aborting the whole run.
* Filters are applied in a fixed order so the reported counts do not
  double-count codes that fail more than one filter.
* Fee fields in the daily feed (`手续费`, `日增长率`, ...) are read but
  **not** persisted in the v0.1 schema.

## History first, snapshot only for gaps

For each selected fund we fetch **both** full-history indicators from
`fund_open_fund_info_em`. Separately, the daily snapshot exposes at most two
recent dated NAV columns such as `2026-09-24-单位净值` /
`2026-09-24-累计净值`. We use those snapshot values **only** for
(date, metric) slots where history has no value:

* a non-NULL historical value **always wins** over a snapshot value for the
  same date and metric;
* a snapshot value can fill a NULL date/metric (a missing historical date,
  or a missing metric on an existing date);
* a stored non-NULL NAV is **never** revised by a later snapshot; only
  another non-NULL historical value can revise it;
* when a snapshot value and a non-NULL historical value disagree on the same
  (date, metric), the historical value is stored and the disagreement is
  reported in the run output (`discrepancies`) — never silently overwritten.

## Known precision limitation

AKShare applies `pd.to_numeric` to NAV values, so by the time we see them
they are already floats. Storing them in `NUMERIC(18,6)` does **not** restore
the original source decimals — it only gives a reproducible storage
representation. Our policy is:

* convert via `Decimal(str(value))`;
* round to six fractional digits with `ROUND_HALF_UP`;
* reject non-finite, non-positive, or overflowing values.

If you need source-decimal fidelity, this API cannot provide it.

## Layout

```
fofoca-data/
  pyproject.toml
  src/fofoca_data/
    sql/001_fofoca.sql            -- dedicated schema (3 tables, packaged)
    cli.py                        -- argparse entry point
    provider.py                   -- thin AKShare wrapper (rate-limited)
    eligibility.py                -- name ∩ daily snapshot candidate selection
    history.py                    -- fetch & merge the two history indicators,
                                     reconcile with daily snapshot
    db.py                         -- persistence (atomic per-fund writes)
    backfill.py                   -- orchestration
    errors.py, normalize.py
  tests/                          -- offline fixtures + disposable-DB tests
  scripts/
    probe_akshare.py              -- read-only live shape probe
    run_integration_tests.sh      -- disposable local PG cluster + pytest
  deploy/
    README.md                     -- Aliyun deployment guide (manual, no timer)
    deploy.sh                     -- operator-run deployment script
```

## Prerequisites

* Python **3.12** (target host) — see `pyproject.toml` (`requires-python`).
* A dedicated PostgreSQL database and a **least-privilege** role that owns
  only the three fofoca tables.
* Network access to EastMoney/AKShare endpoints: `fund_name_em`,
  `fund_open_fund_daily_em`, and `fund_open_fund_info_em` must all be
  reachable. If any of them is blocked, `backfill` fails closed.

## Setup (local development)

```sh
cd fofoca-data
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

Configuration is via environment variables only — no credentials in the
repository:

```sh
cp .env.example .env      # then edit .env; .env is git-ignored
export FOFOCA_DATABASE_URL="postgresql://fofoca_app:...@127.0.0.1:5432/fofoca"
# optional, seconds between consecutive provider calls (default 1.0)
export FOFOCA_REQUEST_DELAY_SECONDS=1.0
```

## Usage

### 1. Create the tables (idempotent)

```sh
fofoca-data init-db
```

### 2. Historical backfill

All current eligible candidates:

```sh
fofoca-data backfill
```

Only specific funds (still subject to the eligibility check):

```sh
fofoca-data backfill --code 000001 --code 166009
```

The command prints a JSON report and exits:

* `0` — selection succeeded and every eligible fund succeeded;
* `1` — selection failed, or at least one eligible fund failed;
* `2` — invalid input (bad code, bad date, reversed range, missing env).

The report includes for each fund: `status`, `partial_coverage` (one history
indicator was empty), `snapshot_fill_count` (date/metric slots filled from
the daily snapshot), `discrepancy_count` + `discrepancies` (same-date
history/snapshot conflicts, history wins), and the observed
`first_data_date`/`last_data_date`.

### 3. Read-only query

```sh
fofoca-data query --code 166009 --start 2024-01-01 --end 2024-12-31
```

Returns stored rows in ascending date order with ISO dates and decimal-string
NAVs (nulls preserved). An unknown code or an empty interval returns an empty
`rows` array. The command performs **no** writes and **no** upstream calls.

## Rate limiting and resource use

* Funds are processed **one at a time**. There is deliberately no
  parallelism: the target host has limited RAM, and parallel EastMoney
  requests are both impolite and likely to be throttled.
* `FOFOCA_REQUEST_DELAY_SECONDS` (default `1.0`) is a fixed sleep between
  consecutive `fund_open_fund_info_em` calls. Increase it if you observe
  throttling.
* Each fund's two full-history responses are held in memory only for the
  duration of that fund's transaction, then released.

## Running tests

Offline tests (no network, no DB):

```sh
pytest tests/
```

Disposable-DB integration tests. The script spins up a private PostgreSQL
cluster under `/tmp`, creates a random disposable database, runs the full
suite against it, and stops the cluster. It never touches Ariadne:

```sh
./scripts/run_integration_tests.sh
```

## Live provider probe

`scripts/probe_akshare.py` performs read-only live calls and prints the
actual column names / shapes of `fund_name_em`, `fund_open_fund_daily_em`
and the two history indicators. It also runs the live eligibility selector
end-to-end and reports the resulting candidate counts. Use it to verify
upstream shapes before a broad backfill:

```sh
python scripts/probe_akshare.py
```

## Deployment

See `deploy/README.md`. The deployment is **manual** (operator-run script),
uses a dedicated database role with credentials injected from outside the
repository, and **does not install any systemd timer**. It does not modify
Ariadne resources.

## Exit codes

| code | meaning |
| ---: | --- |
| 0 | success |
| 1 | selection failure or ≥1 eligible fund failed |
| 2 | invalid input / missing configuration |

## License

Proprietary; internal use.
