## Why

Stored fund NAV currently requires direct database access or the local Python CLI. A small authenticated, read-only HTTP service will make the existing data available remotely without coupling clients to PostgreSQL or expanding the backfill application's responsibilities.

## What Changes

- Add an independent `fofoca-api/` Rust crate in this repository, using Rust edition 2024, Axum, Tokio, SQLx/PostgreSQL, Serde, chrono, rust_decimal, SNAFU, and tracing.
- Provide `GET /funds/{code}/nav?start=YYYY-MM-DD&end=YYYY-MM-DD` with inclusive stored-data queries, decimal-string/null NAV values, and a configurable maximum date span.
- Require one externally configured HTTP Basic account before every request, including unknown paths and unsupported methods; require reverse-proxy HTTPS for production remote access.
- Read only the existing `fund` and `fund_nav_daily` tables using parameterized runtime SQL, a bounded connection pool, and a least-privilege database role. Perform no schema initialization, migrations, writes, or upstream fetching.
- Add isolated tests and English configuration/deployment documentation, with staged implementation checkpoints and a future repository-guide scope update.
- Preserve the Python application, schema, and CLI requirements. Do not add Rust backtesting, a frontend, Python strategies, Java, user/token tables, authentication management APIs, or automatic scheduling/restart policies.

## Capabilities

### New Capabilities

- `fund-data-http-api`: Authenticated remote read-only access to stored fund NAV, including input bounds, lossless responses, configuration, failure handling, and transport/deployment constraints.

### Modified Capabilities

None. `stored-nav-query` remains the existing CLI contract; the new capability follows its data semantics without changing that requirement.

## Impact

- Future implementation adds `fofoca-api/` as a standalone Cargo crate with a committed `Cargo.lock`; no new Git repository or Cargo workspace is needed.
- The API exposes a new HTTP interface and reads the existing dedicated FOFoca PostgreSQL database with separate read-only credentials; existing Python and database objects remain unchanged.
- Deployment requires external environment injection, a single Basic account, and a TLS-terminating reverse proxy for remote production use. Deployment instructions do not authorize changes to production or Ariadne resources.
- Future documentation work will describe both applications in `AGENTS.md` without removing existing Python operational safeguards. This proposal change itself only creates OpenSpec artifacts.
