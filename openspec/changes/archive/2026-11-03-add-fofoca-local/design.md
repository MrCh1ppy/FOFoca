## Context

The repository currently has three separate components: `fofoca-data` is a Python ingestion/query application, `fofoca-api` is the authenticated cloud-side read-only HTTP service, and `fofoca-engine` is a synchronous Rust/PyO3 in-memory backtest library. The engine deliberately has no networking or database dependency. Local Python callers need a single package that can fetch NAV history from `fofoca-api` and use the same Rust-owned engine.

The user decided that the new project is named `fofoca-local`, its internal modules are named `data` and `engine`, and dependency versions/features/lockfile are managed at the `fofoca-local` workspace root. `data` must use Rust `reqwest`; it must call the cloud `fofoca-api`, not the cloud Python ingestion process or PostgreSQL directly.

## Goals / Non-Goals

**Goals:**

- Provide one local Rust workspace and one Python distribution/import surface for `fofoca_local.data` and `fofoca_local.engine`.
- Let `data` fetch the existing authenticated inclusive NAV range endpoint with reqwest and preserve dates, NULLs, and exact decimal strings.
- Reuse the current engine implementation and its established daily settlement contract without maintaining a duplicate ledger or settlement code.
- Centralize dependency version/feature choices and the lockfile in `fofoca-local`.
- Make API URL, credentials, and timeouts explicit configuration; never auto-load dotenv or log credentials.

**Non-Goals:**

- Changing, redeploying, or writing to the cloud `fofoca-api`, PostgreSQL, or `fofoca-data` ingestion flow.
- Adding HTTP or runtime dependencies to the pure `fofoca-engine` core.
- Filling missing NAV dates, substituting accumulated NAV, or converting NAV through floating point.
- Implementing a complete strategy runner, report/metrics, persistence, data caching, or scheduling.

## Decisions

### One `fofoca-local` Cargo workspace owns dependency resolution

The root workspace declares shared dependency versions/features and owns `Cargo.lock`; workspace member manifests declare only which workspace dependencies they consume. The `data` and `engine` members remain separated by responsibility. This was chosen over independent Cargo projects because the user wants dependencies managed at the local composition boundary, and over a monolithic crate because module-level dependency isolation remains useful.

### `data` is a reqwest client for the existing cloud API

The client calls `GET /funds/{code}/nav?start=...&end=...`, authenticates with configured Basic credentials, uses bounded connect/overall timeouts, checks HTTP status and response identity/range, and deserializes rows into owned typed records. `unit_nav` remains an optional exact decimal string; absent dates are not synthesized. `accumulated_nav` is preserved separately and never substituted for unit NAV.

Because the engine and Python usage are synchronous, the initial client uses reqwest's blocking API rather than introducing Tokio into the local call path. Async can be reconsidered only if a concrete concurrent workload appears.

### Compose the existing engine; do not fork its implementation

The workspace's `engine` member must reuse the current `fofoca-engine` source and tests, not copy them. During apply, choose the least disruptive workspace integration that actually gives the local workspace authority over dependency resolution and its lockfile. Preserve the public Python methods, exact values, exception/result semantics, and CPython 3.12 wheel contract. If preserving the current import name or relocating the package conflicts with a single local wheel, stop and resolve the packaging choice rather than silently shipping duplicate native engines.

### One Python-facing local package

The intended import surface is `from fofoca_local import data, engine`. The data API exposes a typed `fetch_nav_history(code, start, end, config=...)` operation; engine exposes the existing `BacktestSession`. Data returns normalized records, and the Python strategy/driver remains responsible for deciding which date-specific market and orders to pass to the engine. `data` never mutates the engine session.

### Explicit configuration and safe failures

The client receives an explicit configuration object containing a validated base URL, Basic username/password, and bounded timeout values. It does not read process environment or dotenv implicitly. Errors are typed by configuration, transport, HTTP status, and response decoding/contract failure; diagnostics must not contain credentials or Authorization headers. Redirects must not forward credentials to an untrusted origin.

## Risks / Trade-offs

- [Risk] Moving the current engine crate can break its maturin layout or import name → preserve and test the real CPython 3.12 installed-wheel contract before removing the old package path; keep a single native implementation.
- [Risk] Reqwest blocking features can increase binary size and TLS/platform complexity → use the existing project's rustls preference and validate the target wheel/build environment.
- [Risk] API data ends at its latest stored row, which may lag the requested end date → return only stored rows and make coverage explicit; do not synthesize NAV or claim the requested end date was priced.
- [Risk] Basic credentials could leak through logs or redirects → never log request headers/config, disable unsafe cross-origin credential forwarding, and test redaction/redirect behavior.
- [Risk] Workspace integration can create duplicate lockfiles/dependency resolution → require one authoritative `fofoca-local/Cargo.lock` for local builds and document the supported build commands.

## Migration Plan

1. Create workspace/member boundaries and integrate existing engine source without semantic changes.
2. Establish the unified maturin/Python package contract and prove the current wheel tests still pass.
3. Implement and test the typed reqwest NAV client against mocked HTTP responses, including auth, status, malformed responses, NULL NAV, gaps, timeout, and redaction.
4. Run Rust default/Python-feature checks, installed CPython 3.12 wheel contract, and OpenSpec validation.
5. Retire old build entry points only after the replacement package is verified; cloud services and deployed API remain untouched. Rollback is source/build-layout rollback; no production data migration is involved.

## Resolved implementation choices

- Retain the engine crate at `../fofoca-engine` as an explicit workspace member. Its dependency declarations inherit the local workspace policy; remove its standalone lockfile and maturin entry point. Existing Rust source/tests stay authoritative in place.
- Ship one `fofoca_local._native` extension. Move the existing typed engine facade into `fofoca_local.engine`; `fofoca_engine` is a re-export shim, including a `sys.modules` alias for the old private native import. Preserve original exception/native class module identities. Installed-wheel tests assert identity and reuse the unchanged complete engine contract. Do not co-install the obsolete engine distribution.
- Require explicit Rust/Python `Config` only, with no environment convenience constructor. Reject every redirect (including same-origin), disable ambient proxies, accept only HTTP(S) origins without embedded credentials or URL suffixes, and bound `0 < connect_timeout <= timeout <= 300` seconds.
- Preserve exact nullable strings at both language boundaries. Python returns frozen `History`/`NavRow` dataclasses, with real `datetime.date` values and a tuple of rows. Rust validates the complete response before conversion; data calls release the GIL and never touch session state.
