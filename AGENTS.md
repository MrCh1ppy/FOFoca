# Repository guide

## Scope and source of truth
- The application is `fofoca-data/`: Python >=3.12, setuptools `src/` layout,
  AKShare pinned to `1.18.97`; CLI entry point: `fofoca_data.cli:main`.
- Use `openspec/specs/` for requirements; `openspec/changes/archive/` is design history.
  `resumable-historical-nav-backfill/spec.md` supersedes older three-table/sync-state
  requirements still present in other specs. The current schema has two tables.
- `docs/fofoca-data-v0.1.md` is outdated: do not restore daily timers or legacy sync state.
- Edit `fofoca-data/src/fofoca_data/`, not generated `build/lib/` or `*.egg-info/`.

## Development commands
Run all commands below from `fofoca-data/`:
```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest tests/
.venv/bin/python -m pytest tests/test_provider.py::test_context_injects_timeout_into_plain_get
./scripts/run_integration_tests.sh
```
- The integration script needs `.venv/bin/python` and PostgreSQL tools
  `initdb`, `pg_ctl`, `psql`, and `pg_isready` on `PATH`; it uses a disposable DB.
- DB tests skip unless both `FOFOCA_INTEGRATION_DB=true` and
  `FOFOCA_TEST_DATABASE_URL` are set; the script sets both.
- The script ends with `exec pytest`; do not assume its EXIT trap stops the cluster.
  After tests, cleanup may require `pg_ctl -D /tmp/fofoca-it-pg -m fast stop`
  (use the actual data directory if overridden).

## Wiring and data invariants
- `cli.py` wires `AkshareProvider` to `backfill.run_backfill`; selection lives in
  `eligibility.py`, history fetching/merging in `history.py`, persistence in `db.py`.
- Selection fails closed using the name/daily feed intersection, exact open purchase
  and redemption statuses, and known non-money-market types; `--code` cannot bypass it.
- Backfill deliberately uses one sequential worker, with no parallel fetches.
- Fetch/validate before writing; commit each fund's metadata and NAV atomically.
  Failed attempts leave no partial writes or failure-state rows; prior commits survive.
- Historical non-NULL NAV may revise stored values; snapshots only fill missing values.
  Missing metrics never erase stored values; do not insert all-NULL NAV rows.
- `--target-date` skips stored `MAX(nav_date) >= target` before history fetching/writing;
  its lookup transaction ends before fetching. Without it, re-fetch full histories.
  A maximum is not proof of complete history; final reconciliation shortfalls fail the run.
- `query` only reads stored rows over inclusive dates; it never fetches upstream.

## Environment and operational safety
- Export `FOFOCA_DATABASE_URL`; the application does not automatically load `.env`.
- Use a dedicated FOFoca database/user, never Ariadne's database or resources.
- `init-db` creates missing `fund`/`fund_nav_daily` tables and never drops tables.
  Legacy `fund_sync_state` removal is only a manual DBA migration with verified backups.
- For deployment and recovery, follow `fofoca-data/deploy/README.md`; runs are manual,
  unscheduled, and must not introduce automatic restarts or timers.

## Rust read-only API (`fofoca-api/`)
- `fofoca-api/` is an independent edition-2024 Cargo crate (committed `Cargo.lock`)
  exposing authenticated read-only HTTP access to the same stored `fund`/
  `fund_nav_daily` tables. It never initializes schema, migrates, writes, or fetches
  upstream; the Python application and its safeguards above remain authoritative.
- Validate from the repository root: `cargo check/test --locked --manifest-path
  fofoca-api/Cargo.toml` need no database or environment. The disposable-PostgreSQL
  integration suite is separately gated: `fofoca-api/scripts/run_integration_tests.sh`
  (requires `initdb`/`pg_ctl`/`psql`/`pg_isready`; isolates environment and HOME
  in `/tmp/fofoca-api-suite-XXXXXX`, with unique `fofoca-api-it-*` child clusters).
  Cleanup covers success and normal failure/panic, not SIGKILL/host failure;
  failed stops retain diagnostics/live files and fail the script. Never clean
  another run's resources.
- The API runs with its own dedicated SELECT-only role; deploy per
  `fofoca-api/deploy/README.md` with external secret injection and a TLS-terminating
  reverse proxy. Manual operation only — no timers or auto-restart.
  SQLx's default `PG*`/`PGPASSFILE`/home `.pgpass` fallbacks are allowed;
  isolate the service environment/account/home from unintended settings and
  credentials. Application auto-dotenv loading remains forbidden.
