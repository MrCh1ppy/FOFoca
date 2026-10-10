# fofoca-api

`fofoca-api` is a small, authenticated, **read-only** HTTP service that
exposes the stored FOFoca fund NAV data held in PostgreSQL. It is an
independent Rust companion to the Python `fofoca-data` application: it
reads the existing `fund` and `fund_nav_daily` tables and never
initializes schema, migrates, writes, or fetches upstream data.

- Rust edition 2024, standalone crate in this repository (no workspace,
  no separate Git repository), with a committed `Cargo.lock` for
  reproducible builds.
- Stack: Axum, Tokio, SQLx/PostgreSQL (runtime queries only — no
  compile-time database introspection), Serde, chrono `NaiveDate`,
  rust_decimal `Decimal`, SNAFU, tracing.

## Endpoint

```
GET /funds/{code}/nav?start=YYYY-MM-DD&end=YYYY-MM-DD
```

- `code`: exactly six ASCII digits (leading zeros are significant).
- `start` / `end`: each must appear exactly once, in strict `YYYY-MM-DD`
  form, denoting a real calendar date. Bounds are inclusive; a same-day
  interval counts as one day. `start` after `end` is rejected.
- The inclusive span in calendar days must not exceed
  `FOFOCA_API_MAX_RANGE_DAYS` (default 3660, roughly ten years — a
  bounded-history design choice, not a domain constant). The unique
  `(fund_id, nav_date)` storage key bounds result cardinality by the
  requested span, so no pagination is needed.

### Authentication

Every request — including unknown paths, unsupported methods, HEAD, and
OPTIONS — requires HTTP Basic authentication with the single externally
configured account. There are no public health, documentation, or error
bypasses.

Failures (`401`) always carry
`WWW-Authenticate: Basic realm="fofoca-api", charset="UTF-8"` and a
generic body that never echoes supplied material:

```json
{"error":"unauthorized"}
```

The `Basic` scheme is matched case-insensitively; credentials are decoded
as standard base64 UTF-8 and split at the first colon, so passwords may
contain colons. Credential comparison uses fixed-length SHA-256 digests
and the reviewed `subtle` constant-time comparison; hashing work still
depends on input length. Digests are only an in-memory comparison, not
password storage or a password KDF.
Authentication runs before routing, request extraction, and any database
access; unauthenticated requests never acquire a pool connection.

### Responses

Success (`200`) follows the Python CLI's stored-data semantics:

```json
{"code":"166009","start":"2026-09-23","end":"2026-09-25","rows":[
  {"nav_date":"2026-09-23","unit_nav":"1.234567","accumulated_nav":null},
  {"nav_date":"2026-09-25","unit_nav":null,"accumulated_nav":"2.345678"}
]}
```

- Rows are ascending by `nav_date` and include only stored rows within
  the inclusive bounds. Date gaps are never synthesized, and missing
  metrics are never substituted (`unit_nav` and `accumulated_nav` stay in
  their own fields).
- Present NAV values are exact decimal strings with six fractional
  digits, matching the stored `NUMERIC(18,6)` scale (e.g. `"1.000000"`,
  `"999999999999.123456"`). No floating point is involved.
- Missing metrics are JSON `null`.
- An unknown code, a fund without NAV rows, or an interval with no stored
  rows returns `200` with `"rows": []` — not `404`.
- Stored funds are returned regardless of their current fund
  status/eligibility; this is a stored-history query, not a backfill
  selection.

Errors are small stable JSON bodies:

| Status | Body | Meaning |
| --- | --- | --- |
| 400 | `{"error":"invalid_request"}` | Bad code/date/query shape, reversed or excessive range |
| 401 | `{"error":"unauthorized"}` | Missing/malformed/duplicate/wrong credentials (challenge header attached) |
| 404 | `{"error":"not_found"}` | Unknown path (after authentication) |
| 405 | `{"error":"method_not_allowed"}` for OPTIONS; no HEAD body by HTTP semantics; empty framework body for other unsupported methods | Non-GET on the query endpoint (after authentication; `Allow` attached) |
| 500 | `{"error":"internal_error"}` | Database/internal failure; never a fabricated empty `200` |

Error responses never contain source chains, SQL diagnostics, connection
URLs, credentials, or raw query strings. Internal errors retain their
source chain inside the process for diagnosis.

One completion log per request records request id, safe method label,
matched route template (or `<unmatched>`), status, and elapsed time,
including authentication failures, default 405s and extractor rejections.
Internal failures add only allowlisted category/operation context, never
source diagnostics. Unauthenticated requests are marked unmatched because
authentication wraps the complete router service before route matching.

### Examples

```sh
curl -u reader:password \
  'http://127.0.0.1:8080/funds/166009/nav?start=2026-09-23&end=2026-09-25'

# No credentials -> 401 with WWW-Authenticate challenge.
curl -i 'http://127.0.0.1:8080/funds/166009/nav?start=2026-09-23&end=2026-09-25'

# Reversed bounds -> 400 after authentication.
curl -u reader:password \
  'http://127.0.0.1:8080/funds/166009/nav?start=2026-09-25&end=2026-09-23'
```

## Configuration

All configuration is read from externally injected environment variables.
**The application never loads `.env` files automatically.**

| Variable | Required | Default | Contract |
| --- | --- | --- | --- |
| `FOFOCA_DATABASE_URL` | yes | — | Nonempty SQLx-compatible PostgreSQL **URL** (e.g. `postgresql://user:pass@host:5432/dbname`) for a dedicated read-only role. SQLx requires URL syntax; libpq keyword DSNs such as `host=... dbname=...` are rejected. |
| `FOFOCA_API_BASIC_USERNAME` | yes | — | Single-account username; nonempty, no colon or control characters. |
| `FOFOCA_API_BASIC_PASSWORD` | yes | — | Single-account password; nonempty, no control characters; preserved verbatim (colons allowed). |
| `FOFOCA_API_BIND` | no | `127.0.0.1:8080` | Socket address for the HTTP listener. |
| `FOFOCA_API_MAX_RANGE_DAYS` | no | `3660` | Maximum inclusive calendar-day span per query; must be a positive representable integer. |

Startup fails closed before listening when a required setting is missing,
empty, or whitespace-only, when a supplied optional setting is empty or
invalid, or when PostgreSQL cannot be reached within the startup bound.
Diagnostics identify the setting but never echo its value. Secrets must
not be committed; inject them via your process manager, container
secrets, or an explicit operator-sourced `env` file that is never read by
the application itself.

Only `postgres`/`postgresql` URL schemes are accepted. SQLx's default
compatibility behavior is intentional: omitted connection settings may
inherit `PG*` environment values, and an absent URL password may use
`PGPASSFILE` or the service user's home `.pgpass`. Passwordless local
connections are valid. The application does not clear process environment
or suppress these SQLx fallbacks. Isolate the service environment and
account/home, removing unintended PG settings and credential files or
explicitly providing only dedicated FOFoca read-only values. See
`deploy/README.md`; this is not automatic `.env` loading.

### Resource bounds

- Connection pool: at most 5 connections, 5-second connect/acquire
  timeout, 10-second PostgreSQL statement timeout per connection, and a
  read-only session declaration (`default_transaction_read_only=on`) as
  defense in depth underneath the SELECT-only role grants.
- SQLx statement logging is disabled directly on connection options.
  Additionally, the tracing subscriber only enables
  this crate's targets, and `RUST_LOG` is sanitized so it can tune the
  crate's verbosity but can never re-enable dependency targets (sqlx,
  hyper, tokio, …) or span/field selectors.

## Build and test

All commands run from the repository root:

```sh
cargo check  --locked --manifest-path fofoca-api/Cargo.toml
cargo test   --locked --manifest-path fofoca-api/Cargo.toml
cargo fmt    --manifest-path fofoca-api/Cargo.toml --all -- --check
cargo clippy --locked --manifest-path fofoca-api/Cargo.toml --all-targets -- -D warnings
```

Builds and the default test suite require **no database and no
environment configuration**. The disposable-PostgreSQL integration suite
is separately gated and never starts a database during a plain
`cargo test`:

```sh
fofoca-api/scripts/run_integration_tests.sh
```

The script requires `initdb`, `pg_ctl`, `psql`, and `pg_isready` on
`PATH`, creates a private `/tmp/fofoca-api-suite-XXXXXX` sandbox with an
empty environment and private HOME/TMPDIR, and runs database tests against
unique `fofoca-api-it-*` child clusters. Clusters are cleaned up on success
and normal failure/panic; stop failures retain diagnostics and make the
script fail rather than delete live files. SIGKILL/host failures cannot
guarantee cleanup. The script fails if tools are missing or its own cluster
directories leak, and never scans or cleans another run's resources. See
`deploy/README.md` for details.

## Standalone-crate trade-offs

The crate lives in this repository with its own `Cargo.lock`, outside a
Cargo workspace: with one Rust crate a workspace adds no value, and the
lockfile alone makes dependency resolution reproducible. If more Rust
crates appear, a workspace can be introduced then. The crate shares code
with nothing; semantic parity with the Python CLI (`code`/`start`/`end`/
`rows`, ISO dates, decimal strings, nulls) is maintained by contract
tests rather than by shared libraries.

## Transport security

HTTP Basic credentials are reusable and base64 is not encryption. Remote
production access must go through HTTPS terminated at a reverse proxy
forwarding to the default local bind; plain HTTP is only acceptable for
localhost and in-process testing. See `deploy/README.md` for the full
deployment contract.
