## Why

Local Python strategies currently have to obtain NAV data outside the repository and call the standalone engine separately. A single `fofoca-local` composition layer is needed to fetch stored history from the deployed read-only `fofoca-api` and drive the Rust-owned backtest without duplicating account or data semantics.

## What Changes

- Add a `fofoca-local` Rust workspace as the local composition boundary, with `data` and `engine` members and one lockfile/dependency policy.
- Add a `data` member using Rust `reqwest` to authenticate to and query the remote `fofoca-api` NAV endpoint, preserving exact NAV strings and missing rows.
- Bring the existing Rust/PyO3 backtest engine under the local workspace without copying its implementation or changing its account semantics.
- Expose a unified Python package/import surface for local data access and engine use.
- Keep cloud `fofoca-data`, cloud `fofoca-api`, and the pure settlement engine as separate responsibilities. **BREAKING** for package/build layout only; existing cloud services and API routes remain unchanged.

## Capabilities

### New Capabilities

- `local-data-client`: Authenticated, bounded, read-only remote NAV history access from the local Rust/Python composition layer.
- `local-composition`: Unified local workspace/package structure that composes `data` with the existing Rust-owned engine.

### Modified Capabilities

None. Existing `python-daily-advance` and `fund-data-http-api` requirements remain behaviorally unchanged.

## Impact

- Adds a new `fofoca-local/` Rust workspace, Python package, dependency lockfile, and tests.
- Adds `reqwest`, response deserialization, URL/auth configuration, timeout/error handling, and PyO3 or Python-facing conversion in the local `data` member.
- Requires relocating or workspace-integrating the existing `fofoca-engine` crate while preserving its public behavior and CPython 3.12 wheel contract.
- No database writes, upstream backfill, cloud deployment, schema migration, or changes to the authenticated `fofoca-api` service.
