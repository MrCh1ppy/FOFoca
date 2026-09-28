# Aliyun deployment guide — fofoca-data (two-table release)

This guide covers the **manually-triggered, unscheduled** deployment of
`fofoca-data` on the Aliyun host. It:

* creates a dedicated PostgreSQL database and a **least-privilege** role;
* installs the application under an unprivileged system user;
* injects credentials from **outside** the Git repository;
* provides manual `init-db`, `backfill`, and `query` entry points;
* documents the **manual, DBA-run** removal of the legacy
  `fund_sync_state` table (there is deliberately **no** application command
  for this);
* **does not** install any systemd timer/cron entry (unscheduled, manual
  runs only), and **does not** configure automatic restarts;
* **does not** touch Ariadne's database, services, files, nginx config, or
  the host's `vm.swappiness=0` setting.

If you need scheduled runs in a later version, that is a separate change
requiring its own review.

## Assumptions

* Aliyun host with Python **3.12** (`python3.12 --version`) and `psql`
  available.
* You have sudo (or a DBA) to create the database and role, and to create a
  system user.
* The host can reach the EastMoney endpoints used by AKShare
  (`fund_name_em`, `fund_open_fund_daily_em`, `fund_open_fund_info_em`).
* The host shares roughly **1.6 GiB RAM + 2 GiB swap** with Ariadne and
  PostgreSQL. Every memory/elapsed bound below must be chosen from
  **measured free headroom at run time**, not from total RAM.
* You have already cloned this repository to the host (e.g. under
  `/opt/fofoca/FOFoca`) using the separately-authorized Git remote. **Git
  operations themselves are out of scope for this guide.**

## Safety boundary

* **Never** point `FOFOCA_DATABASE_URL` at Ariadne's database.
* **Never** run `deploy/deploy.sh` as the Ariadne user or against Ariadne's
  PostgreSQL database/schema.
* The integration-test database used by `scripts/run_integration_tests.sh`
  is disposable and lives only under `/tmp` on the dev machine; it has no
  relationship to this deployment.

## One-time setup (DBA / sudo)

1. Pick names and a strong password. Example (do **not** commit these):

   ```sh
   FOFOCA_DB_NAME=fofoca
   FOFOCA_DB_USER=fofoca_app
   FOFOCA_DB_PASSWORD='<generate-a-long-random-string>'
   ```

2. Create the role and database (run as a PostgreSQL superuser):

   ```sql
   CREATE ROLE fofoca_app LOGIN PASSWORD '<generate-a-long-random-string>';
   CREATE DATABASE fofoca OWNER fofoca_app;
   -- Optional hardening:
   REVOKE ALL ON DATABASE fofoca FROM PUBLIC;
   ```

3. Create an unprivileged system user and application directory:

   ```sh
   sudo useradd --system --home /opt/fofoca --shell /usr/sbin/nologin fofoca || true
   sudo mkdir -p /opt/fofoca
   sudo chown fofoca:fofoca /opt/fofoca
   ```

4. Place this repository at `/opt/fofoca/FOFoca` (owned by `fofoca:fofoca`).

5. Create the secrets file **outside** the repository:

   ```sh
   sudo install -m 0600 -o fofoca -g fofoca /dev/null /opt/fofoca/fofoca-data.env
   sudoedit /opt/fofoca/fofoca-data.env
   ```

   Contents (adjust host/port/db/user/password):

   ```ini
   FOFOCA_DATABASE_URL=postgresql://fofoca_app:<password>@127.0.0.1:5432/fofoca
   FOFOCA_REQUEST_DELAY_SECONDS=1.0
   # Best-effort per-request transport bounds (defaults shown):
   FOFOCA_CONNECT_TIMEOUT_SECONDS=10
   FOFOCA_READ_TIMEOUT_SECONDS=60
   ```

   The file must be `0600 fofoca:fofoca` and **must not** live inside the
   Git working tree.

## Deploy / upgrade

Run the idempotent deployment script as the `fofoca` user (or via sudo -u):

```sh
sudo -u fofoca /opt/fofoca/FOFoca/fofoca-data/deploy/deploy.sh
```

The script:

* verifies Python 3.12 is available;
* creates/updates `/opt/fofoca/.venv`;
* installs the package in non-editable form;
* runs `fofoca-data init-db` against the configured database to ensure the
  **two** tables (`fund`, `fund_nav_daily`) exist. `init-db` is idempotent
  and **never drops anything** — it neither removes nor recreates the
  legacy `fund_sync_state` table.

It does **not** start a backfill automatically, and it does **not** install
any timer.

## Removing the legacy `fund_sync_state` table (manual DBA runbook)

The application works correctly whether the stale table is present or
absent — it is simply no longer read or written, so removal can be done at
any convenient maintenance window. The removal is **manual SQL run by a
DBA**; there is deliberately **no** `fofoca-data` subcommand that drops
tables, and `init-db` never performs it.

1. **Verify the target.** Confirm `FOFOCA_DATABASE_URL` points at the
   dedicated fofoca database (not Ariadne), e.g.
   `psql "$FOFOCA_DATABASE_URL" -c 'SELECT current_database(), current_user'`.
2. **Take an independently restorable backup and actually verify it:**
   ```sh
   mkdir -p /opt/fofoca/backups
   pg_dump "$FOFOCA_DATABASE_URL" -Fc -f /opt/fofoca/backups/fofoca-pre-migration.dump
   pg_dump "$FOFOCA_DATABASE_URL" --table=fund_sync_state -Fc \
       -f /opt/fofoca/backups/fund_sync_state-pre-migration.dump
   ```
   `pg_restore --list` only proves the archive **header is readable** — it
   does **not** prove the data can be restored. Verify for real: restore
   into a **scratch database** and compare row counts:
   ```sh
   createdb fofoca_restore_check
   pg_restore -d fofoca_restore_check /opt/fofoca/backups/fofoca-pre-migration.dump
   psql -d fofoca_restore_check -c \
     "SELECT 'fund', count(*) FROM fund
      UNION ALL SELECT 'fund_nav_daily', count(*) FROM fund_nav_daily
      UNION ALL SELECT 'fund_sync_state', count(*) FROM fund_sync_state;"
   # counts must match the live database (next step), then:
   dropdb fofoca_restore_check
   ```
   Record live row counts for comparison before and after:
   ```sql
   SELECT 'fund', count(*) FROM fund
   UNION ALL SELECT 'fund_nav_daily', count(*) FROM fund_nav_daily
   UNION ALL SELECT 'fund_sync_state', count(*) FROM fund_sync_state;
   ```
3. **Drop only the old table (no CASCADE):**
   ```sql
   DROP TABLE fund_sync_state;
   ```
   Do **not** use `CASCADE`, do **not** `DROP TABLE IF EXISTS` blindly as
   part of any script, and do not touch `fund`/`fund_nav_daily`.
4. **Verify after:** re-run the count query (fund/NAV counts unchanged,
   `fund_sync_state` absent), spot-check a few NAV rows with `query`, and
   confirm `init-db` does not recreate the removed table.

If the backup or its scratch-restore verification fails, **stop** — do not
drop anything, and do not proceed to backfill with an unverified database.

### Rollback and version compatibility

The retained tables are never rewritten by this migration. If you ever roll
back to a **pre-migration (v0.1) build**, note that the old code path
*re-creates* `fund_sync_state` on `init-db` and expects its rows. The safe
order is therefore:

1. restore the saved `fund_sync_state` dump (or the full pre-migration
   dump) **first**;
2. only then deploy the older build.

Restoring the old table **after** the old build has already re-created it
risks a restore conflict or stale rows. Never restore silently as part of
routine `init-db`.

## Manual operations

As the `fofoca` user, with the env file loaded. Use the virtualenv's entry
point explicitly — do **not** rely on `fofoca-data` being on the system
`PATH`.

Ordinary full-history re-fetch (manual recheck path):

```sh
sudo -u fofoca bash -c 'set -a; . /opt/fofoca/fofoca-data.env; set +a; \
  /opt/fofoca/.venv/bin/fofoca-data backfill --code 000001 --code 166009'
```

Resumable recovery run — skip funds whose stored `MAX(nav_date)` is already
at/above the threshold. The recovery target date is supplied per run; the
documented recovery invocation is:

```sh
sudo -u fofoca bash -c 'set -a; . /opt/fofoca/fofoca-data.env; set +a; \
  /opt/fofoca/.venv/bin/fofoca-data backfill --target-date 2026-09-24'
```

Without `--target-date`, every selected fund is fully re-fetched even if its
stored max is already later than any recovery threshold.

Read-only query:

```sh
sudo -u fofoca bash -c 'set -a; . /opt/fofoca/fofoca-data.env; set +a; \
  /opt/fofoca/.venv/bin/fofoca-data query --code 166009 --start 2024-01-01 --end 2024-12-31'
```

## Resource bounds and host health (shared low-RAM host)

* Sequential, single-worker design: one fund at a time, one request at a
  time. Peak memory is bounded by one fund's two history DataFrames plus the
  catalog directories (tens of MB). No timeout threads or worker pools are
  ever created.
* Run every broad run under **measured, conservative FOFoca-only limits**,
  sized from free headroom at run time — for example a transient systemd
  scope with a memory high-water mark and an outer wall-clock limit:

  ```sh
  sudo systemd-run --unit=fofoca-backfill-manual --collect \
      -p MemoryMax=512M -p MemoryHigh=448M \
      -p RuntimeMaxSec=21600 \
      -p EnvironmentFile=/opt/fofoca/fofoca-data.env \
      -p StandardOutput=append:/opt/fofoca/fofoca-backfill-manual.json \
      -p StandardError=append:/opt/fofoca/fofoca-backfill-manual.log \
      --uid=fofoca --working-directory=/opt/fofoca \
      /opt/fofoca/.venv/bin/fofoca-data backfill --target-date 2026-09-24
  ```

  (`EnvironmentFile` loads the DSN from the `0600` secrets file — never put
  secrets on the command line, where any local user can read them from the
  process list. The `append:` paths keep the final JSON report and the live
  `[fofoca] ...` progress log in stable, per-run files; `append:` requires
  systemd ≥ 240. On an older systemd, pass only `EnvironmentFile` and
  redirect stdout/stderr from a wrapper shell instead. Use a fixed run name
  so a cut-off segment's results stay recoverable, and confirm the files
  exist before starting the next segment. The memory values are examples —
  measure `free -m`, swap usage, and PostgreSQL/Ariadne usage first.)
* The in-application connect/read timeouts are **best-effort** transport
  bounds only (see `README.md`): they do not cover DNS edge cases or
  post-response parsing, and they are never applied to SQL transactions. The
  external `RuntimeMaxSec`/process stop is the only hard bound, and an
  abrupt kill may preclude the final JSON report.
* **Before** a broad run: check `free -m`, swap usage, `dmesg -T | grep -i
  oom`, and that Ariadne and PostgreSQL are healthy.
* **During**: watch the live `[fofoca] ...` progress lines (each fund logs
  START, per-indicator STAGE, and SUCCESS/FAILED/TIMEOUT/SKIPPED with
  elapsed seconds), plus memory/swap.
* **After**: check `dmesg` for OOM evidence, Ariadne/PostgreSQL health, and
  read the final report's `summary` and `below_target_codes`.
* On a new OOM, repeated fetch timeouts, or a sustained stall (no per-fund
  progress lines), **stop and diagnose**. Do not auto-restart, and never
  infer progress or completion from an absent report — reconcile from the
  persisted per-fund `MAX(nav_date)` (`query` or SQL) before retrying.
* Do not change `vm.swappiness` or any Ariadne/PostgreSQL limit as part of
  this runbook.

## Verification checklist (before any broad run)

* `python3.12 --version` on the host.
* `/opt/fofoca/fofoca-data.env` exists, has mode `0600`, and is owned by
  `fofoca:fofoca`. The deployment script **refuses** to proceed otherwise.
* `/opt/fofoca/.venv/bin/fofoca-data init-db` succeeds and the schema holds
  exactly the two retained tables (the legacy table, if still present, was
  handled by the migration above).
* `scripts/probe_akshare.py` reaches `fund_name_em`,
  `fund_open_fund_daily_em`, and `fund_open_fund_info_em` from the host,
  parses them, and reports a sensible candidate count. If any of these is
  blocked or unparseable, `backfill` fails closed by design — fix the
  network/provider, not the eligibility rule.
* Host memory/swap/OOM and Ariadne/PostgreSQL health checks pass.
* **Small-sample gate:** a few eligible funds with dates on both sides of
  the threshold, e.g.
  `sudo -u fofoca bash -c 'set -a; . /opt/fofoca/fofoca-data.env; set +a; /opt/fofoca/.venv/bin/fofoca-data backfill --code 000001 --code 166009 --target-date 2026-09-24'`,
  completes with truthful per-code outcomes (skip vs. attempt), preserved
  NAV values, live logs, and a correct final report — **before** the
  all-eligible run is explicitly authorized.
* The full run itself is a **separate, explicit** operator decision, made
  only after the sample and the migration checks above pass.

## Understanding the final report

* `summary`: `selected`, `attempted`, `skipped`, `succeeded`, `failed` —
  and in target mode `below_target` plus `below_target_codes`, the eligible
  selected funds whose **persisted** `MAX(nav_date)` is still below the
  target after the run (including funds with no stored NAV).
* A fund that fetched and committed successfully but whose history ends
  before the target is counted `succeeded` **and** listed below target; the
  run exits nonzero. A zero exit requires: no attempted failures **and**
  (target mode) no eligible selected code still below target.
* Exit `0` is not proof of contiguous or correct history — the target date
  is a work threshold, and the max date is a skip heuristic. Use no-target
  `--code` runs for manual spot review.

## Recovery after an abrupt kill

If the process was killed (external limit, OOM, manual stop):

1. Do **not** restart automatically. Check host OOM evidence and service
   health first.
2. The final JSON report may be missing — never assume which funds were
   reached. Reconcile from the database:
   ```sql
   SELECT f.code, MAX(n.nav_date)
   FROM fund f LEFT JOIN fund_nav_daily n ON n.fund_id = f.id
   GROUP BY f.code ORDER BY 2 NULLS FIRST;
   ```
3. Re-run with `--target-date <T>`; already-covered funds are skipped
   before any fetch, and previously committed NAV rows are preserved
   (per-fund atomic commits, idempotent upserts).

## Rollback

Because the application performs only idempotent writes into its **own**
database, rollback is:

1. Stop any running `fofoca-data` process (there is no service/timer).
2. Leave the database intact for inspection or retry — NAV rows are
   idempotent; re-running the backfill will upsert, not duplicate.
3. Revert to the previously reviewed code revision. **Mind the version
   boundary:** a pre-migration (v0.1) build re-creates `fund_sync_state` on
   `init-db` and expects its rows. Restore the saved `fund_sync_state` dump
   **before** deploying the older build (see the rollback note in the
   migration runbook above), never after it has already re-created the
   table.

Dropping the retained schema is an explicit operator action and is **not**
performed by any script here:

```sql
-- DBA only, irreversible:
-- DROP TABLE IF EXISTS fund_nav_daily, fund CASCADE;
```

## Resource notes

* Default `FOFOCA_REQUEST_DELAY_SECONDS=1.0` keeps the request rate low.
  Increase if you observe throttling; decrease only after measuring.
* A full catalog backfill is roughly **18,600 funds** × two history calls
  each. As a measured baseline, the first broad run completed only ~800
  funds in over an hour, so a full pass is expected to take **tens of hours
  or longer**, not minutes. Run it inside `tmux`/`screen`, `nohup`, or the
  transient systemd scope above, with live output captured to a log file.
* `RuntimeMaxSec` only terminates the scope at the end of a segment; it
  does **not** restart the run. After each segment is cut off, launch the
  next one manually with the same `--target-date` (already-covered funds
  are skipped and committed rows are preserved) and stop only when the
  final report shows no eligible selected fund still below target. Never
  rely on an automatic restart.
