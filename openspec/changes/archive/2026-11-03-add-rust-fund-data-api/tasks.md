## 1. Scaffold and startup configuration

- [x] 1.1 Re-read the approved proposal/design/spec, inspect existing Python query/schema, and verify working-tree boundaries; preserve unrelated untracked `AGENTS.md` and reference projects, and do not change Python/schema or production resources.
- [x] 1.2 Create standalone `fofoca-api/` with Rust edition 2024, the specified dependencies, `src/bin/fofoca-api.rs`, minimal `lib` facade/private modules, and `Cargo.lock`; avoid a new workspace, repository, or Service/repository traits.
- [x] 1.3 Add `Result<T, E = Error>`, SNAFU categories/source handling, and external configuration for `FOFOCA_DATABASE_URL`, `FOFOCA_API_BASIC_USERNAME`, `FOFOCA_API_BASIC_PASSWORD`, `FOFOCA_API_BIND`, and `FOFOCA_API_MAX_RANGE_DAYS`, with the documented defaults and no automatic dotenv loading.
- [x] 1.4 Add deterministic config tests for absent/empty/whitespace-only required settings, invalid URL/account/bind, zero/negative/overflow/empty limits, defaults, verbatim password preservation, and secret-free diagnostics; avoid unsafe process-environment mutation in edition-2024 tests.
- [x] 1.5 Run `cargo check --locked --manifest-path fofoca-api/Cargo.toml` and `cargo test --locked --manifest-path fofoca-api/Cargo.toml` without database credentials or a live DB; report scaffold/startup test evidence and limitations to the user before starting phase 2.

## 2. Authentication and HTTP contract

- [x] 2.1 Implement the whole-router Basic middleware with one external account, standards-compatible decoding and reviewed constant-time comparison, authenticating before route/method handling, extraction, and pool acquisition.
- [x] 2.2 Register GET `/funds/{code}/nav`, authenticated 404/405 fallbacks, and explicit authenticated HEAD/OPTIONS rejection; normalize domain/extractor errors to safe JSON 400/401/500 responses, with the required Basic challenge on every 401.
- [x] 2.3 Implement strict code/date parsing and checked inclusive range validation; test missing/duplicate/invalid dates, ASCII code constraints, reversed bounds, same-day queries, default/configured limits, and exact-limit versus one-day-over boundaries before DB access.
- [x] 2.4 Add in-process negative authentication tests across query/unknown/unsupported-method paths: missing/wrong credentials, malformed base64/UTF-8, absent separator, duplicate headers, unsupported scheme; verify case-insensitive Basic and password colons, auth-first precedence, valid-auth 404/405, and no public health/docs bypass.
- [x] 2.5 Add allowlisted tracing and source-redaction tests covering secret markers in configuration, Authorization, URIs/query strings, and internal errors; retain internal source chains but omit them from clients/logs.
- [x] 2.6 Run focused HTTP/auth/config tests without a real database; report the authentication/HTTP checkpoint with negative-test results and limitations to the user before starting phase 3.

## 3. Read-only database access and isolated integration

- [x] 3.1 Add the runtime SQLx pool with at most five connections, five-second connection/acquisition bounds, ten-second PostgreSQL statement timeout, and disabled SQL statement logging; connect before listening and fail safely without schema initialization or migration.
- [x] 3.2 Implement the one-code inclusive SELECT joining only `fund`/`fund_nav_daily` with `$1`/`$2`/`$3` bind parameters and ascending dates; decode `NaiveDate` and `Option<Decimal>`, returning CLI-compatible outer fields, exact six-place decimal strings, and nulls without floating point or upstream access.
- [x] 3.3 Create an API-specific disposable PostgreSQL integration harness with unique local resources, explicit isolation checks and cleanup on success/failure; do not target Ariadne/production or reuse the Python `exec pytest`/EXIT-trap harness as-is, and do not modify that existing script.
- [x] 3.4 Set up existing-schema fixtures using an isolated test owner and run API requests under a separate role limited to CONNECT/schema USAGE/SELECT on the two tables; verify mutation/schema-creation denial and unchanged schema/data after startup and requests.
- [x] 3.5 Add integration coverage for inclusive bounds, ordering, leading-zero codes, maximum `NUMERIC(18,6)` precision, trailing zeros, both nullable metrics, date gaps, unknown codes, funds without NAV, empty intervals, and stored funds regardless of current eligibility/status.
- [x] 3.6 Exercise unavailable database/startup failure, pool acquisition timeout, SELECT/statement-timeout failures, generic 500 versus empty 200, and absence of credentials in captured response/logs; verify unauthenticated/invalid requests do not acquire a pool connection.
- [x] 3.7 Run the isolated integration suite and verify the disposable cluster/resources are cleaned up; report the DB-read/integration checkpoint and any unvalidated items to the user before starting phase 4.

## 4. Documentation, scope guide, and final validation

- [x] 4.1 Write English API/configuration/build documentation covering every named environment variable, no auto-dotenv, SQLx URL syntax versus libpq DSNs, GET request/response examples, auth-first errors, range/default rationale, decimal/null/gap semantics, and standalone-crate trade-offs.
- [x] 4.2 Write English deployment/testing instructions for dedicated FOFoca SELECT-only grants, external secret injection without committed secrets, reverse-proxy production HTTPS and local upstream bind, localhost HTTP tests, disposable integration cleanup, and rollback without data/schema changes; do not modify production or introduce timers/restart policy.
- [x] 4.3 At implementation time, review the existing untracked `AGENTS.md` with the user-owned content preserved, then make only the agreed scope extension for the Rust API and its validation commands; retain all Python/manual-operation/Ariadne safeguards and leave reference projects untouched.
- [x] 4.4 Run `cargo fmt --manifest-path fofoca-api/Cargo.toml --all -- --check`, `cargo clippy --locked --manifest-path fofoca-api/Cargo.toml --all-targets -- -D warnings`, and `cargo test --locked --manifest-path fofoca-api/Cargo.toml` with no required build-time DB; rerun the separately gated disposable PostgreSQL integration suite.
- [x] 4.5 Run `.venv/bin/python -m pytest tests/` from `fofoca-data/` if its documented environment is available, without contacting production; inspect the final diff to confirm unchanged Python/schema, no secrets, no unrelated work, and committed-intended `Cargo.lock` (do not make a Git commit unless separately requested).
- [x] 4.6 Run `openspec validate add-rust-fund-data-api --strict`, report final test/validation evidence, cleanup and remaining risks to the user, and distinguish implementation completion from any separately authorized production deployment.

Final review validation: all 24 tasks are checked. Ordinary Cargo tests ran
75 library tests, 5 binary tests and 2 local no-database startup-failure
checks; 10 integration cases returned early because the DB gate was unset
(Cargo reports these as passing tests, not ignored tests). The isolated
script then enabled the gate and passed all 12 integration cases, including
10 real PostgreSQL/cleanup cases and the 2 startup checks. The script
reported successful cluster cleanup and no API test sandboxes remained.
Fmt/check, locked Cargo check, all-targets clippy with warnings denied, and
strict OpenSpec validation passed. Python was not changed or rerun; its
previous 118 passed / 49 skipped result remains the reported evidence.
SQLx default PG/passfile compatibility is explicitly permitted by the
approved deployment-isolation decision; application auto-dotenv remains
forbidden. No production deployment or Git commit was performed.
