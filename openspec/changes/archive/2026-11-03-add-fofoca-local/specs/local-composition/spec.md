## ADDED Requirements

### Requirement: Local workspace owns dependency resolution
The repository SHALL provide a `fofoca-local` Rust workspace with `data` and `engine` responsibilities, one authoritative dependency version/feature policy, and one workspace lockfile for local builds. Workspace members SHALL declare only dependencies they use and SHALL obtain versions/features from the workspace policy.

#### Scenario: Reproducible local workspace build
- **WHEN** a developer builds/tests the local workspace with its committed lockfile
- **THEN** all local members resolve from the single `fofoca-local` dependency graph without requiring cloud services or a database

### Requirement: Reuse the authoritative backtest engine
The local `engine` module SHALL reuse the existing Rust session and settlement implementation without maintaining a fork or second account ledger. The current initialization, account snapshot, `advance_day(orders, market)`, exact-value, business rejection, and lifecycle behavior SHALL remain unchanged.

#### Scenario: Existing engine contract remains valid
- **WHEN** the local package is built and its installed CPython 3.12 wheel contract is run
- **THEN** all existing engine behaviors and daily advancement results remain compatible through the documented local import surface

### Requirement: Expose one local Python package
The local distribution SHALL expose `fofoca_local.data` and `fofoca_local.engine` as the local caller surface. The data module SHALL return NAV history without mutating engine state; strategy code SHALL explicitly pass date-specific orders and market values to the engine.

#### Scenario: Compose fetched NAV with a session
- **WHEN** Python fetches NAV history through `fofoca_local.data` and uses it with `fofoca_local.engine`
- **THEN** the caller controls daily order/market submission and the Rust engine remains the sole account-state owner

### Requirement: Preserve cloud service boundaries
The local project SHALL call only the authenticated read-only `fofoca-api` for remote NAV data. It SHALL NOT import or execute cloud `fofoca-data` backfill internals, access PostgreSQL directly, alter cloud API behavior, or trigger upstream fetch/write operations.

#### Scenario: Local data access remains read-only
- **WHEN** local history is fetched
- **THEN** the only remote operation is an authenticated API read and no database or backfill mutation is initiated
