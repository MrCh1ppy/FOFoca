## ADDED Requirements

### Requirement: Single externally configured Basic account
The API SHALL authenticate every request using one externally configured HTTP Basic username/password before route or method handling, request extraction, or database acquisition. It SHALL NOT offer public health/docs/error bypasses, user/token database storage, management/login APIs, sessions, JWT, or OAuth. Missing, malformed, duplicate, unsupported-scheme, or incorrect Authorization credentials SHALL produce HTTP 401 with `WWW-Authenticate: Basic realm="fofoca-api", charset="UTF-8"` and a generic response that does not echo credentials. The Basic scheme SHALL be case-insensitive; decoded UTF-8 credentials SHALL use the first colon as the username/password separator, permitting colons in passwords.

#### Scenario: Authentication failures on every HTTP surface
- **WHEN** a request has no credentials, wrong credentials, invalid base64, invalid UTF-8, no separator, duplicate Authorization headers, or a non-Basic scheme, on the query route, an unknown path, or an unsupported method
- **THEN** the API returns 401 with the Basic challenge, without revealing supplied values or accessing PostgreSQL

#### Scenario: Authentication precedes input rejection
- **WHEN** an unauthenticated query request also contains an invalid fund code or malformed dates
- **THEN** the response is 401 rather than an input-validation error and no database connection is acquired

#### Scenario: Valid account on unknown path or unsupported method
- **WHEN** valid credentials accompany an unknown path or a non-GET method including HEAD or OPTIONS on the known query endpoint
- **THEN** the unknown path returns 404 and the unsupported endpoint method returns 405, without creating an authentication bypass

#### Scenario: Standards-compatible credentials
- **WHEN** a request supplies the configured valid account with a case-insensitive Basic scheme and a password containing a colon
- **THEN** it passes authentication and proceeds to routing and validation without changing the password

### Requirement: Validated bounded NAV query endpoint
The API SHALL expose `GET /funds/{code}/nav?start=YYYY-MM-DD&end=YYYY-MM-DD` for exactly one six-ASCII-digit code and inclusive calendar-date bounds. Both date parameters SHALL occur exactly once and SHALL be real dates in the exact `YYYY-MM-DD` format. Missing, duplicate, malformed, reversed, or excessive bounds and invalid codes SHALL return HTTP 400 after authentication and before database access. The inclusive span SHALL be calculated in calendar days without overflow and SHALL NOT exceed the configured positive `FOFOCA_API_MAX_RANGE_DAYS`, defaulting to 3660. This default is a bounded-history design choice, not a required domain constant.

#### Scenario: Invalid query input
- **WHEN** an authenticated request contains a non-six-digit or non-ASCII-digit code, a missing/duplicate date parameter, a non-ISO date, an impossible date, or start after end
- **THEN** the API returns 400 with a safe input error and does not query the database

#### Scenario: Inclusive span boundary
- **WHEN** an authenticated valid request has exactly the configured maximum inclusive days, or exceeds it by one day
- **THEN** the boundary request proceeds and the excessive request returns 400 before database access

#### Scenario: Same-day interval and external override
- **WHEN** the maximum range is configured to a valid positive integer and an authenticated request has equal start/end dates
- **THEN** that interval counts as one calendar day and is accepted

### Requirement: Lossless stored NAV response
For valid authenticated queries the API SHALL return HTTP 200 JSON with outer fields `code`, `start`, `end`, and `rows`, following the existing Python CLI's stored-data semantics. Each row SHALL contain `nav_date`, `unit_nav`, and `accumulated_nav`, in ascending date order, with only stored rows within inclusive bounds. Dates SHALL be ISO `YYYY-MM-DD`; present NAV SHALL be exact decimal strings preserving the existing `NUMERIC(18,6)` value and six fractional digits, and missing NAV SHALL be JSON null. The API SHALL NOT use floating point, fill missing dates/metrics, substitute accumulated NAV for unit NAV, reapply current backfill eligibility, or fetch upstream. An unknown code, fund without NAV, or interval with no matching rows SHALL yield `rows: []`, not 404.

#### Scenario: Inclusive ordered rows with precision and null
- **WHEN** stored NAV exists at both requested bounds and outside the interval, one metric is NULL, and a present value is `999999999999.123456`
- **THEN** the response returns only in-range rows in ascending date order, ISO dates, the exact string `"999999999999.123456"`, and null for the missing metric

#### Scenario: Missing trading date and unit NAV
- **WHEN** stored dates have a gap and a row has only accumulated NAV
- **THEN** no gap date is synthesized, unit NAV remains null, and accumulated NAV is returned only in its own field

#### Scenario: No stored matches
- **WHEN** a valid request refers to an unknown code, a fund without NAV, or an empty stored interval
- **THEN** it returns 200 with the requested code/start/end and an empty rows array without upstream calls or writes

### Requirement: Parameterized read-only bounded PostgreSQL access
The API SHALL read only the existing `fund` and `fund_nav_daily` tables through runtime SQLx PostgreSQL SELECT queries with bound code/date parameters. It SHALL perform no schema initialization, migration, application-table mutation, or upstream fetching at startup or request time. The runtime role SHALL have only the required CONNECT/schema USAGE/SELECT privileges and no table ownership or mutation/schema-creation privileges. The connection pool SHALL allow at most five connections, use a finite five-second connection/acquisition timeout, and apply a ten-second PostgreSQL statement timeout. Database failures SHALL return a sanitized 500 rather than a fabricated empty result.

#### Scenario: Dedicated read-only role
- **WHEN** the API queries an isolated FOFoca database under a role with SELECT on the two existing tables but no mutation or schema-creation privileges
- **THEN** the query succeeds, test mutation attempts with that role are denied, and stored schema/data remain unchanged by API startup and requests

#### Scenario: Pool or database failure
- **WHEN** a valid authenticated request cannot acquire a connection within the finite bound or its SELECT fails/times out
- **THEN** it returns a generic 500, records only safe failure context, and does not return a successful empty rows array

#### Scenario: Runtime SQL without build-time database
- **WHEN** the crate is built or its non-database tests run without a database URL or reachable PostgreSQL
- **THEN** compilation and those tests do not require database introspection or schema setup

### Requirement: External configuration and fail-closed startup
The API SHALL consume external environment configuration without automatically loading `.env`. `FOFOCA_DATABASE_URL` SHALL be a required nonempty SQLx-compatible PostgreSQL URL for the dedicated read-only database role. `FOFOCA_API_BASIC_USERNAME` and `FOFOCA_API_BASIC_PASSWORD` SHALL configure the required single account; whitespace-only values SHALL be rejected, the username SHALL exclude colon/control characters, the password SHALL exclude control characters, and accepted credentials SHALL be preserved verbatim. `FOFOCA_API_BIND` SHALL default to `127.0.0.1:8080`; `FOFOCA_API_MAX_RANGE_DAYS` SHALL default to 3660 and accept only positive representable integers. Unset optional settings SHALL use defaults; supplied empty/invalid settings SHALL fail. Missing or invalid required configuration, invalid bind/limit, or failed database connection SHALL terminate startup before listening without disclosing configuration values. Secrets SHALL NOT be committed.

SQLx's default `PG*` environment and `PGPASSFILE`/home `.pgpass` fallbacks SHALL be permitted for omitted connection settings. URL schemes SHALL be restricted to `postgres` and `postgresql`. The application SHALL NOT require a database password or mutate process environment to suppress SQLx fallbacks. Deployment SHALL isolate the API environment and service account/home from unintended PG settings and credential files, permitting only intentional dedicated FOFoca read-only configuration. This compatibility SHALL NOT introduce automatic `.env` loading.

#### Scenario: SQLx compatibility under an isolated deployment environment
- **WHEN** the operator intentionally supplies dedicated FOFoca read-only connection fallbacks through PG settings or a protected passfile, or uses a passwordless local connection
- **THEN** SQLx's default fallback behavior is allowed, the application does not load `.env` or mutate process environment, and unrelated PG settings and home credential files must be excluded by deployment isolation

#### Scenario: Safe defaults with externally injected secrets
- **WHEN** valid required settings are injected and optional settings are absent
- **THEN** startup uses the local bind, 3660-day maximum, and bounded pool, without reading `.env` or changing the database schema

#### Scenario: Invalid configuration or unavailable database
- **WHEN** required credentials/URL are absent, empty, whitespace-only, or malformed, optional settings are supplied empty/invalid, the limit is zero/negative/unrepresentable, or PostgreSQL cannot be connected within the startup bound
- **THEN** startup exits unsuccessfully before listening with sanitized diagnostics that do not echo supplied values

### Requirement: Classified errors and credential-safe observability
The implementation SHALL provide `Result<T, E = Error>` and SNAFU error categories for invalid requests (400), authentication failures (401), and internal/database failures (500), retaining relevant safe operation context and source chains internally. Client errors SHALL expose only safe stable messages/categories and SHALL NOT expose source chains or raw database diagnostics. Tracing and startup diagnostics SHALL NOT expose Authorization headers, account credentials, database credentials/URLs, secret-bearing config, raw request query strings, or raw source diagnostics; SQLx statement logging SHALL be disabled. HTTP routing errors SHALL retain authenticated 404/405 behavior independently of those categories.

#### Scenario: Source error contains a secret marker
- **WHEN** an internal/database/configuration failure has sensitive text in its source or configured values
- **THEN** response and captured logs contain only safe status/category/operation information, omit that text, and the internal error retains its source when present

#### Scenario: Safe request observation
- **WHEN** successful and rejected requests are traced
- **THEN** logs can identify status, elapsed time, method, and matched-route or unmatched marker without logging credentials or raw URIs/query strings

### Requirement: Independent crate and safe operational boundary
The implementation SHALL live in an independent Rust edition-2024 `fofoca-api/` Cargo crate in the existing repository with a committed `Cargo.lock`, a `src/bin` entry point, a `lib` facade, private implementation modules, and free functions preferred over Service/repository trait hierarchies. It SHALL use Axum, Tokio, SQLx/PostgreSQL, Serde, chrono `NaiveDate`, rust_decimal `Decimal`, SNAFU, and tracing. It SHALL preserve the existing Python application and schema. English documentation SHALL describe external configuration, read-only grants, the endpoint, standalone-crate validation commands, isolated PostgreSQL tests, and HTTPS termination at a reverse proxy with a local upstream. Remote production access SHALL require HTTPS; plain HTTP SHALL be limited to local testing/upstream use. This change SHALL NOT alter production resources, introduce timers or automatic restart policy, or use Ariadne/production databases for tests.

#### Scenario: Local tests and production transport
- **WHEN** operators follow the deployment/testing documentation
- **THEN** localhost integration HTTP is supported, remote production Basic credentials use HTTPS with a locally bound upstream, and disposable test database setup/cleanup cannot target Ariadne or production

#### Scenario: Independent build and compatible Python application
- **WHEN** the new crate is validated with Cargo fmt/clippy/test commands and existing Python behavior is checked
- **THEN** no new Git repository, unnecessary workspace, Python requirement change, or schema migration is required, and the crate's lockfile makes its dependency resolution reproducible
