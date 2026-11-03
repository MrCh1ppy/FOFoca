## Context

`fofoca-data/src/fofoca_data/db.py::query_nav_range` joins `fund_nav_daily` to `fund`, binds one code and inclusive dates, and orders by `nav_date`. `cli.py::_cmd_query` emits `code`, `start`, `end`, and `rows`, with ISO dates and decimal strings/null. The bundled `sql/001_fofoca.sql` has two tables, nullable `NUMERIC(18,6)` NAV columns, and a `(fund_id, nav_date)` primary key. `resumable-historical-nav-backfill` supersedes historical references to a third sync-state table. The API reads these objects; it does not change Python eligibility, backfill, query, or schema behavior.

## Goals / Non-Goals

**Goals:** Provide a small remote read-only JSON interface, authenticate every HTTP request using one configured Basic account, preserve stored-data semantics and decimal precision, bound database/resource usage, and document safe isolated validation and production HTTPS prerequisites.

**Non-Goals:** Rust backtesting, frontend work, Python strategies, Java, another Git repository, user/token storage, login/session/JWT/OAuth flows, management APIs, upstream fetching, database writes/migrations, production deployment changes, timers, and automatic restart policies.

## Decisions

### Standalone crate and small module boundaries

Add `fofoca-api/Cargo.toml` with edition 2024 and commit its `Cargo.lock`. Use Axum, Tokio, SQLx with PostgreSQL/chrono/rust_decimal support, Serde, chrono `NaiveDate`, rust_decimal `Decimal`, SNAFU, and tracing. Runtime `sqlx::query`/`query_as` with bind parameters avoids compile-time database introspection; do not use database-dependent query macros or require `DATABASE_URL` for builds.

Follow the requested ravel-style lightweight conventions: `src/bin/fofoca-api.rs` handles the executable boundary, `src/lib.rs` exposes the minimal application facade, and implementation modules remain private. Configuration, authentication, HTTP/error handling, and database reads can be small private modules with free functions; a concrete state struct contains immutable configuration and `PgPool`. Export `Result<T, E = Error>`. Do not introduce `Service`/repository traits, dependency-injection hierarchies, or speculative framework layers. A Cargo workspace adds no value with one Rust crate; introduce one only if future Rust crates need it. Keep the crate in this repository rather than splitting versioning across repositories.

### One protected HTTP surface

Use `GET /funds/{code}/nav?start=YYYY-MM-DD&end=YYYY-MM-DD`. The successful JSON shape matches the CLI:

```json
{"code":"166009","start":"2026-09-23","end":"2026-09-24","rows":[{"nav_date":"2026-09-24","unit_nav":"1.234567","accumulated_nav":null}]}
```

Attach authentication as the outermost middleware around the complete router, including fallback and method-not-allowed handling, not as route-only middleware. Authenticate before routing, query/path extraction, or database acquisition. No health, documentation, error, HEAD, or OPTIONS bypass is public. Explicitly reject HEAD on the query endpoint after valid authentication rather than relying on Axum's implicit GET-to-HEAD behavior; supported query method is GET only. Unknown paths then return 404 and other unsupported endpoint methods return 405.

Accept exactly one Authorization header containing a well-formed Basic value. Decode using an existing small standards-compatible Basic/base64 helper, require valid UTF-8 `username:password`, split at the first colon, and reject duplicate headers, invalid base64, missing separator, unsupported schemes, or incorrect credentials with the same 401 response. Use case-insensitive auth scheme matching and a reviewed constant-time credential comparison rather than custom cryptographic code. Account credentials are externally injected, never persisted. Username must be nonempty, contain no colon/control characters, and password must be nonempty and contain no control characters; password colons remain valid. Every 401 includes `WWW-Authenticate: Basic realm="fofoca-api", charset="UTF-8"` and never echoes credentials. JWT, user tables, and login endpoints are unnecessary for the selected single-account use case.

### Validation and exact stored-data projection

Require a code of exactly six ASCII digits. Require one `start` and one `end` query parameter, each strictly formatted as a real `YYYY-MM-DD` date; reject missing, duplicate, malformed, or reversed bounds with 400 before database access. Use `NaiveDate` and checked inclusive calendar-day arithmetic: `(end - start).num_days() + 1`. Reject spans exceeding `FOFOCA_API_MAX_RANGE_DAYS`.

Choose a default maximum of **3660 inclusive calendar days** as a design choice, not a user-mandated number: approximately ten years supports practical history requests while the unique daily key bounds result cardinality to at most the requested days for one code. A positive externally configured integer can change that ceiling. No pagination or generated dates are needed for this bounded single-code interface.

Run one parameterized SELECT with `$1` code, `$2` start, and `$3` end, joining `fund` to `fund_nav_daily` and ordering by `nav_date ASC`. Do not filter current fund status/eligibility; this is a stored-history query, not a new backfill selection. An unknown code, fund with no NAV, or empty interval yields HTTP 200 with `rows: []`. Decode nullable NUMERIC values as `Option<Decimal>` and explicitly serialize present values to decimal strings, retaining six fractional digits for the existing `NUMERIC(18,6)` storage. Never pass through floating point, substitute accumulated NAV for unit NAV, fill date gaps, or fetch upstream. Bind parameters rather than interpolate SQL.

### External configuration and bounded startup/runtime

Read application settings from externally injected environment variables; do not load `.env` automatically:

| Variable | Contract / default |
| --- | --- |
| `FOFOCA_DATABASE_URL` | Required nonempty PostgreSQL URL using a dedicated FOFoca read-only role; SQLx needs URL syntax rather than arbitrary psycopg/libpq keyword DSNs |
| `FOFOCA_API_BASIC_USERNAME` | Required single-account username |
| `FOFOCA_API_BASIC_PASSWORD` | Required single-account password, preserved verbatim |
| `FOFOCA_API_BIND` | Socket address, default `127.0.0.1:8080` |
| `FOFOCA_API_MAX_RANGE_DAYS` | Positive representable integer, default `3660` |

Unset optional settings use defaults; supplied empty or invalid settings fail startup. Reject empty/whitespace-only required values, invalid PostgreSQL URL/bind, unrepresentable limits, and malformed account configuration before listening. Error messages identify the setting but not its value. Do not derive `Debug` for secret-bearing config or log environment values. Reusing the existing DB variable avoids duplicate configuration names; deployments must supply the API's read-only role rather than the Python writer's credentials.

Keep pool controls as simple finite constants initially: at most 5 connections, 5 seconds for connection/pool acquisition, and a PostgreSQL statement timeout of 10 seconds per connection. Establish the pool successfully before listening and fail sanitized startup if unavailable. Connection/session timeout settings are allowed; application table access remains SELECT-only. Do not initialize a schema or migrate at startup. Deployment requires CONNECT and schema USAGE as needed plus SELECT on only `fund` and `fund_nav_daily`, no ownership, mutation, sequence, or schema-creation privileges; a default read-only session is defense in depth, not a substitute for grants. No separate runtime pool-setting surface is needed yet.

Explicitly allow only `postgres` and `postgresql` URL schemes. Preserve SQLx's default connection-option compatibility: omitted URL fields may fall back to `PG*` environment settings, and SQLx may read `PGPASSFILE` or the operating-system user's home `.pgpass` when a password is absent. Passwordless local connections remain valid; do not require a URL password, mutate process environment, upgrade/patch SQLx, or build a custom pure-options parser. Operators must isolate the API environment and service account/home, clearing unintended `PG*` settings and credential files or intentionally supplying only dedicated FOFoca read-only values. This dependency fallback is distinct from application auto-dotenv loading, which remains forbidden.

### Errors and observability without secret disclosure

Use SNAFU error categories mapped to 400 (invalid request), 401 (authentication), and 500 (database/internal failures). Keep safe operation context and underlying `source` within the internal error chain; do not serialize the chain, raw SQLx diagnostics, connection URL, Authorization header, username, or password to clients or logs. Routing 404/405 remain distinct from these domain categories. Normalize extractor rejections to 400, and use small stable JSON error bodies such as `{"error":"invalid_request"}`, `{"error":"unauthorized"}`, and `{"error":"internal_error"}`.

Tracing records allowlisted fields: request identifier, method, matched route template (or a fixed unmatched marker), status, elapsed time, and safe error category/operation. Avoid raw URIs/query strings, headers, secret-bearing `Debug`, and automatic source-chain formatting; disable SQLx statement logging. Log startup failures without raw source details. This retains internal causes for reasoning while exposing only sanitized operational context. Do not make a DB outage look like an empty successful result.

### Validation and deployment boundary

First validate scaffold/config without a DB, then authenticate and exercise the router using in-process tests (no real DB required for rejection paths), then test real SQL/Decimal decoding against disposable PostgreSQL. Cover negative authentication on valid/unknown paths and unsupported methods, rejection before DB access, precision/null/date gaps, inclusive bounds, unknown codes, pool/query failures, and a real SELECT-only role whose attempted mutation is denied.

Use a new API-specific isolated harness or explicit documented disposable-cluster commands with reliable cleanup. The Python integration script ends in `exec pytest`, so its shell EXIT trap cannot be assumed to stop PostgreSQL; do not reuse it as-is or expand this change into fixing it. The new harness must retain control of cleanup, including on test failure, and verify isolation from Ariadne/production. Fixture setup runs with an isolated test owner; the API runs with a separate read-only role. Build/lint/unit tests do not require a database.

The integration script runs with an empty environment and private HOME/TMPDIR under `/tmp/fofoca-api-suite-XXXXXX`, with unique `fofoca-api-it-*` child clusters, excluding real credentials and ambient PG settings. Cleanup covers success and normal failure/panic, not SIGKILL/host failure; failed stops retain the specific cluster and diagnostics rather than deleting live files. Never scan or clean another run's resources. Verify effective read-only privileges, including PUBLIC/member grants.

Production remote traffic uses HTTPS terminated by a reverse proxy forwarding to the default local HTTP bind. Plain HTTP is acceptable for localhost/in-process integration tests, not remote production Basic credentials. Document protected proxy configuration, bounded resources, manual operation, and secret injection without committed secrets. Proxy logs must independently omit Authorization, credentials, and raw URIs/query strings; application sanitization cannot sanitize proxy logs. Do not modify production, add timers, prescribe auto-restart, or change existing backfill recovery policies.

## Risks / Trade-offs

- [Basic credentials are reusable and base64 is not encryption] → Require production HTTPS, local upstream binding, one externally rotated account, and no credential logging; per-user authorization is deliberately out of scope.
- [Middleware/fallback ordering or implicit HEAD handling could bypass the contract] → Test the complete router with unauthenticated and authenticated unknown paths, HEAD, OPTIONS, and unsupported methods.
- [A larger externally configured range increases response memory] → Keep a modest default, validate positive representable limits, document tuning, and bound the pool and query execution.
- [Runtime SQL cannot be type-checked against PostgreSQL during build] → Verify real schema/NUMERIC decoding and readonly grants with isolated integration tests.
- [SQLx or source diagnostics may contain sensitive values] → Use allowlisted logs and generic public errors, keep sources internal, and assert secret markers are absent in error/log tests.
- [Existing deployment URL may use libpq-only syntax or writer credentials] → Document SQLx PostgreSQL URL syntax and explicitly provision the read-only role outside application startup.

## Migration Plan

1. Approve these artifacts; no application implementation is performed during proposal generation.
2. Scaffold and validate the standalone crate, then report the startup/compile checkpoint to the user before continuing.
3. Implement and validate authentication/HTTP rejection behavior, then report the checkpoint before continuing.
4. Implement SELECT-only reads and run isolated PostgreSQL integration tests, then report the checkpoint before continuing.
5. Write English API/build/deployment documentation and carefully extend the repository guide's application scope without overwriting unrelated existing work. Run final validations and report completion evidence.
6. Any actual deployment is a separate operator-authorized action: provision only dedicated FOFoca readonly access, inject credentials, and configure TLS proxying. No schema or Python migration is required. Rollback stops/removes the API and its proxy exposure; Python data and backfill remain unchanged.

## Open Questions

No blocking product decisions remain. Exact compatible dependency versions and the small Basic parser/constant-time helper are implementation selections to verify against Axum/SQLx APIs; they must not change the stated HTTP or security contract.
