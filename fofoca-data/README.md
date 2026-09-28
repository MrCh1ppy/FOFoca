# fofoca-data

One-off historical NAV backfill for Chinese mutual funds whose current
`fund_open_fund_daily_em` snapshot reports **open purchase and open
redemption**, from AKShare 1.18.97 into a dedicated PostgreSQL database.

This project is intentionally **standalone**: it does not read from, write to,
or otherwise touch Ariadne's database, services, or deployment. There is **no
timer, no schedule, no daily incremental sync** — only an explicit
operator-run historical backfill plus a read-only query.

## Scope

* Source: AKShare 1.18.97 (`fund_name_em`, `fund_open_fund_daily_em`,
  `fund_open_fund_info_em(indicator="单位净值走势" | "累计净值走势")`).
* Store: a separate PostgreSQL database with exactly **two** tables (`fund`,
  `fund_nav_daily`). There is deliberately **no** sync-state table: whether a
  fund has coverage is derived from `fund_nav_daily` itself, never from a
  separate success flag.
* Operations: `init-db`, `backfill` (with optional `--target-date`),
  `query`. Nothing else. In particular there is **no CLI command that drops
  tables**: removing the legacy `fund_sync_state` table from an existing
  database is a manual, DBA-run migration (verified external backup +
  explicit SQL), documented in `deploy/README.md`.

The candidate set is a **pragmatic, current** NAV-subscription/redemption
proxy for this platform. It is **not** proof of off-exchange trading, **not**
an Alipay inventory, and **not** a strict T+1 confirmation. A fund that is
open today and closed tomorrow simply will not be selected tomorrow; the
spec does not try to reconstruct past eligibility.

## What "eligible" means

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
  **not** persisted.

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

## Missing vs. invalid NAV cells

Genuinely **missing** historical cells (`None` / pandas `NaN`) on otherwise
valid rows are treated as *absent*, not as errors:

* every row's date is still validated, and duplicate dates still fail the
  fund — including duplicates among missing-value rows;
* a missing cell simply contributes no value for its (date, metric) slot,
  and a stored non-NULL value for that slot is **never erased**;
* if **one** indicator has no usable NAV at all (empty response, or only
  missing cells) the fund succeeds with `partial_coverage: true`;
* if **both** indicators have no usable NAV the fund **fails**, even when
  dated snapshot NAV exists;
* a date with no usable historical metric and no valid dated snapshot fill
  produces **no** stored row — all-NULL rows are never inserted.

By contrast, any **present** value that is malformed (including blank
strings), non-finite (`inf`/`NaN` inside a Decimal), non-positive, or too
large for `NUMERIC(18,6)` fails the whole fund.

## Resumable recovery with `--target-date`

`backfill` accepts an optional `--target-date YYYY-MM-DD` (a real calendar
date; it is never inferred from today):

* for each selected eligible fund (including `--code` runs), the stored
  `MAX(fund_nav_daily.nav_date)` is read first — in a short read-only
  transaction that is fully committed **before** any network fetch;
* if the stored maximum is at least the target, the fund is reported
  `SKIPPED` before either history request, with no fetches and no writes;
* a missing fund row, a fund row with no NAV rows, or a maximum below the
  target causes **both** complete history indicators to be fetched (AKShare
  supplies complete histories only — there is no date-filtered request);
* a failed lookup **aborts** the run; it is never interpreted as "no stored
  coverage";
* without `--target-date`, every eligible selected fund gets the ordinary
  full-history re-fetch — even if its stored maximum is already later than
  any recovery threshold. `backfill --code ...` without a target remains the
  manual recheck path.

**The target is a work threshold, not a guarantee.** A single late stored
date may mask holes or stale values; `MAX(nav_date)` is a scheduling
heuristic, not proof of contiguous or correct history.

After the attempts, target mode re-reads persisted maxima and lists every
selected eligible code still below the target (including funds with no
stored NAV) as `below_target_codes`, separately from failed attempts. A
fund that fetched and committed successfully but whose history ends before
the target counts as `succeeded` **and** appears in `below_target_codes` —
the run exits nonzero and never claims completion while any eligible code
remains below the target or any attempted fund failed.

The documented recovery invocation is:

```sh
fofoca-data backfill --target-date 2026-09-24
```

Run a small `--code ... --target-date 2026-09-24` sample first (see
`deploy/README.md`).

## Live progress and the final report

During the run, flushed single-line progress events go to **stderr**
(`[fofoca] START/STAGE/SUCCESS/FAILED/TIMEOUT/SKIPPED code=...`), including
per-indicator stages and elapsed seconds. All free-form text is sanitized so
credentials can never reach the log.

On normal completion the JSON report on **stdout** contains: `target_date`
(if supplied), selection counts, per-fund outcomes (`status` ∈
`SUCCESS|FAILED|SKIPPED`, `error_class`, `stored_max_nav_date`, elapsed
time, discrepancies, ...), a summary with **selected / attempted / skipped /
succeeded / failed**, and — in target mode — `below_target_codes`.

Exit codes:

| code | meaning |
| ---: | --- |
| 0 | selection succeeded, every attempted fund succeeded, and (target mode) no eligible selected code remains below the target |
| 1 | selection failed, ≥1 attempted fund failed, a target shortfall exists, or reconciliation could not be verified |
| 2 | invalid input / missing configuration |

Skipped funds are not fresh successes; a process exit is never reported as
target completion by itself.

## Fetch timeouts (best-effort) and the hard external bound

The installed AKShare 1.18.97 issues plain `requests.get(...)` calls with
**no timeout and no retries** for all three endpoints we use
(`fund_open_fund_info_em` performs exactly one such request per indicator —
both history indicators share the same `pingzhongdata/{code}.js` payload
shape). `AkshareProvider` therefore wraps `requests.api.get`/`.request`
while active and injects a finite `timeout=(connect, read)` into any call
that does not set its own. Defaults: **10 s connect / 60 s read**,
configurable via `FOFOCA_CONNECT_TIMEOUT_SECONDS` and
`FOFOCA_READ_TIMEOUT_SECONDS`.

This is explicitly a **best-effort** bound:

* it runs everything on the **single calling thread** — no timeout threads,
  no worker pools, and no overlapping fetches; a timed-out request raises
  `requests.exceptions.ConnectTimeout`/`ReadTimeout`, the fund is logged as
  a sanitized timeout failure, and the run may continue with the next fund;
* DNS resolution and post-response work (e.g. AKShare's `py_mini_racer` JS
  evaluation of a huge payload) can evade the socket timeout; it is **not**
  a hard wall-clock cancellation of arbitrary blocked library calls, and we
  never pretend to kill a blocked fetch;
* **no timeout is ever applied to SQL transactions** — database writes
  complete or roll back on their own terms.

The hard bound for a stalled run is an **external, manually controlled
process limit** (systemd unit/runtime limits or the operator stopping the
process), sized from measured host headroom — see `deploy/README.md`. An
abrupt kill may preclude the final JSON report; after any kill, reconcile
from the persisted `MAX(nav_date)` per fund (`query` / direct SQL), never
from logs alone.

## Per-fund atomicity and failure behavior

* one fund's metadata + NAV upserts commit in **one** transaction;
* a failed fetch/validation/write rolls back that fund's attempt only —
  previously committed funds stay committed;
* failures are recorded **only** in live output and the in-memory run
  report — there is no failure table, no failure-only fund row, and no
  success row;
* a broken database connection aborts the whole run (continuing safely is
  impossible), rather than silently skipping funds.

## Two-table schema and the legacy `fund_sync_state`

`init-db` creates `fund` and `fund_nav_daily` if missing, is idempotent, and
**never drops anything** — in particular it never removes (or recreates) the
legacy `fund_sync_state` table on an existing database. The application works
correctly whether the stale table is present or absent (it is simply no
longer read or written).

Removing that table is a **manual, DBA-run migration**, deliberately **not**
implemented as any application/CLI command. There is no
`migrate-drop-fund-sync-state` command; nothing in the package can drop a
table. The full runbook (verified external backup, row-count verification,
explicit `DROP TABLE fund_sync_state;`, post-checks, and the exact
restore-then-downgrade order for rollback) is in `deploy/README.md`.

## Known precision limitation

AKShare applies `pd.to_numeric` to NAV values, so by the time we see them
they are already floats. Storing them in `NUMERIC(18,6)` does **not** restore
the original source decimals — it only gives a reproducible storage
representation. Our policy is:

* convert via `Decimal(str(value))`;
* round to six fractional digits with `ROUND_HALF_UP`;
* reject non-finite, non-positive, or overflowing present values.

If you need source-decimal fidelity, this API cannot provide it.

### Upstream coercion hides malformed values

AKShare converts history NAV columns with
`pd.to_numeric(..., errors="coerce")` **before** we ever see them. That
means the DataFrame handed to us **cannot distinguish** a genuinely missing
upstream cell from a malformed non-empty one that was coerced to `NaN`
upstream (e.g. a placeholder string like `"--"` or a garbled number). We
treat every `NaN`/`None` cell as "missing" and store NULL; we therefore
**cannot reliably detect or report** upstream-side malformed values. This
is an inherent limitation of the API, not a validation gap in our code —
downstream consumers must not read a NULL as proof that the fund had no NAV
that day, only that no usable value reached us. Values that *do* survive
upstream coercion are still strictly validated on our side (real calendar
date, finite, positive, within `NUMERIC(18,6)`).

## Layout

```
fofoca-data/
  pyproject.toml
  src/fofoca_data/
    sql/001_fofoca.sql            -- dedicated schema (2 tables, packaged)
    cli.py                        -- argparse entry point
    provider.py                   -- thin AKShare wrapper (rate-limited,
                                     best-effort transport timeouts)
    eligibility.py                -- name ∩ daily snapshot candidate selection
    history.py                    -- fetch & merge the two history indicators,
                                     reconcile with daily snapshot
    db.py                         -- persistence (atomic per-fund writes,
                                     coverage reads)
    backfill.py                   -- orchestration (target-date resume,
                                     live progress, truthful report)
    errors.py, normalize.py, redact.py
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
  only the fofoca tables.
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
# optional, best-effort per-request transport bounds (defaults 10 / 60)
export FOFOCA_CONNECT_TIMEOUT_SECONDS=10
export FOFOCA_READ_TIMEOUT_SECONDS=60
```

Both timeout values must be finite positive numbers; `0`, negatives, `NaN`
and `inf` are rejected at startup. These bound the requests library's
connect/read waits only (see "Timeout and resource limits" below for what
they do **not** cover).

## Usage

### 1. Create the tables (idempotent)

```sh
fofoca-data init-db
```

### 2. Historical backfill

All current eligible candidates (full-history re-fetch for each):

```sh
fofoca-data backfill
```

Only specific funds (still subject to the eligibility check):

```sh
fofoca-data backfill --code 000001 --code 166009
```

Resumable recovery (skip funds already at/above the threshold):

```sh
fofoca-data backfill --target-date 2026-09-24
```

The command prints live progress on stderr, a JSON report on stdout, and
exits per the table above.

### 3. Read-only query

```sh
fofoca-data query --code 166009 --start 2024-01-01 --end 2024-12-31
```

Returns stored rows in ascending date order with ISO dates and decimal-string
NAVs (nulls preserved). An unknown code or an empty interval returns an empty
`rows` array. The command performs **no** writes and **no** upstream calls.

## Rate limiting and resource use

* Funds are processed **one at a time** by a single worker. There is
  deliberately no parallelism: the target host has limited RAM, and parallel
  EastMoney requests are both impolite and likely to be throttled.
* `FOFOCA_REQUEST_DELAY_SECONDS` (default `1.0`) is a fixed sleep between
  consecutive provider calls. Increase it if you observe throttling.
* Each fund's two full-history responses are held in memory only for the
  duration of that fund's processing, then released.

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
| 0 | success (see the precise definition above) |
| 1 | selection failure, ≥1 attempted fund failed, target shortfall, or unreliable reconciliation |
| 2 | invalid input / missing configuration |

## License

Proprietary; internal use.
