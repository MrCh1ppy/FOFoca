# Aliyun deployment guide — fofoca-data (v0.1)

This guide covers the **manually-triggered, unscheduled** deployment of
`fofoca-data` on the Aliyun host. It:

* creates a dedicated PostgreSQL database and a **least-privilege** role;
* installs the application under an unprivileged system user;
* injects credentials from **outside** the Git repository;
* provides a manual `backfill` and `query` entry point;
* **does not** install any systemd timer/cron entry (v0.1 is one-off);
* **does not** touch Ariadne's database, services, files, or nginx config.

If you need scheduled runs in a later version, that is a separate change
requiring its own review.

## Assumptions

* Aliyun host with Python **3.12** (`python3.12 --version`) and `psql`
  available.
* You have sudo (or a DBA) to create the database and role, and to create a
  system user.
* The host can reach the EastMoney endpoints used by AKShare
  (`fund_name_em`, `fund_open_fund_daily_em`, `fund_open_fund_info_em`).
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
* installs the package in editable or wheel form;
* runs `fofoca-data init-db` against the configured database to ensure the
  three tables exist.

It does **not** start a backfill automatically, and it does **not** install
any timer.

## Manual operations

As the `fofoca` user, with the env file loaded. Use the virtualenv's entry
point explicitly — do **not** rely on `fofoca-data` being on the system
`PATH`:

```sh
sudo -u fofoca bash -c 'set -a; . /opt/fofoca/fofoca-data.env; set +a; \
  /opt/fofoca/.venv/bin/fofoca-data backfill'
```

Or for specific funds:

```sh
sudo -u fofoca bash -c 'set -a; . /opt/fofoca/fofoca-data.env; set +a; \
  /opt/fofoca/.venv/bin/fofoca-data backfill --code 000001 --code 166009'
```

Read-only query:

```sh
sudo -u fofoca bash -c 'set -a; . /opt/fofoca/fofoca-data.env; set +a; \
  /opt/fofoca/.venv/bin/fofoca-data query --code 166009 --start 2024-01-01 --end 2024-12-31'
```

## Rollback

Because v0.1 performs only idempotent writes into its **own** database,
rollback is:

1. Stop any running `fofoca-data` process (there is no service/timer).
2. Leave the database intact for inspection or retry — NAV rows are
   idempotent; re-running the backfill will upsert, not duplicate.

Dropping the schema is an explicit operator action and is **not** performed
by any script here:

```sql
-- DBA only, irreversible:
-- DROP TABLE IF EXISTS fund_sync_state, fund_nav_daily, fund CASCADE;
```

## Verification checklist (before any broad run)

* `python3.12 --version` on the host.
* `/opt/fofoca/fofoca-data.env` exists, has mode `0600`, and is owned by
  `fofoca:fofoca`. The deployment script **refuses** to proceed otherwise.
* `/opt/fofoca/.venv/bin/fofoca-data init-db` succeeds (note: use the
  virtualenv's full path, not the system PATH).
* `scripts/probe_akshare.py` reaches `fund_name_em`,
  `fund_open_fund_daily_em`, and `fund_open_fund_info_em` from the host,
  parses them, and reports a sensible candidate count. If any of these is
  blocked or unparseable, `backfill` fails closed by design — fix the
  network/provider, not the eligibility rule.
* A single-fund trial, e.g.
  `sudo -u fofoca bash -c 'set -a; . /opt/fofoca/fofoca-data.env; set +a; /opt/fofoca/.venv/bin/fofoca-data backfill --code 000001'`,
  completes with `ok: true` and the expected row count.
* `query` returns the rows just written.

## Resource notes

* Sequential, single-process design. Peak memory is bounded by one fund's
  two history DataFrames plus the catalog directories (~tens of MB).
* Default `FOFOCA_REQUEST_DELAY_SECONDS=1.0` keeps the request rate low.
  Increase if you observe throttling; decrease only after measuring.
* A full catalog backfill is thousands of funds × two history calls each;
  plan for hours, not minutes. Run it inside `tmux`/`screen` or `nohup`.
