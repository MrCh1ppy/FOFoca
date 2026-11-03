## 1. Workspace and engine integration

- [x] 1.1 Create the `fofoca-local` workspace root with centralized dependency versions/features and one committed `Cargo.lock`.
- [x] 1.2 Integrate the existing `fofoca-engine` source/tests as the local `engine` member without copying implementation or changing Rust semantics.
- [x] 1.3 Establish one CPython 3.12 maturin distribution exposing `fofoca_local.engine`; decide and test the compatibility policy for the existing `fofoca_engine` import.
- [x] 1.4 Verify default Rust builds, Python-feature builds, existing engine tests, clippy, and installed-wheel contract through the workspace.

## 2. Remote NAV data client

- [x] 2.1 Add workspace-managed blocking `reqwest` and typed response/error dependencies to the `data` member.
- [x] 2.2 Implement explicit validated API configuration for base URL, Basic credentials, and finite connect/overall timeouts without implicit environment or dotenv loading.
- [x] 2.3 Implement `fetch_nav_history(code, start, end)` against the authenticated inclusive API endpoint with safe redirect policy and secret-redacted diagnostics.
- [x] 2.4 Validate status, response identity/range, ordered in-range rows, field types, and exact nullable NAV values; return no partial records on failure.
- [x] 2.5 Expose the typed history result through `fofoca_local.data` without mutating or implicitly advancing the engine session.

## 3. Client contract tests

- [x] 3.1 Add local mock-server tests for successful rows, empty results, null metrics, distinct accumulated NAV, and date gaps.
- [x] 3.2 Add tests for invalid config/input, authentication/status failures, malformed or inconsistent responses, timeouts, and redirect credential protection.
- [x] 3.3 Assert errors/log output never expose Basic username/password or Authorization material.

## 4. Documentation and validation

- [x] 4.1 Document local workspace build/install, explicit API configuration, `fetch_nav_history`, and composing returned rows with `BacktestSession`.
- [x] 4.2 Run workspace formatting, default/all-feature tests and clippy, CPython 3.12 installed-wheel contract, OpenSpec strict validation, and diff checks.
- [x] 4.3 Confirm no changes to cloud services, database schema/data, or production deployment and remove obsolete engine build instructions only after replacement validation.

## Validation evidence

- CPython 3.12.13, PyO3 0.30.1; locked release maturin build produced `fofoca_local-0.1.0-cp312-cp312-manylinux_2_34_x86_64.whl` in `/tmp/opencode/fofoca-local-wheels-312`, installed into a fresh `/tmp/opencode/fofoca-local-test-312` venv.
- Workspace `cargo fmt --all --check`, locked `check`/`test`/`clippy --all-targets -- -D warnings` passed for default and all features. Python-feature commands explicitly used `PYO3_PYTHON=python3.12`. Each configuration passed all 23 original engine unit tests, 7 unchanged engine integration tests, and 5 client test groups (including multiple malformed/status/redirect scenarios).
- Isolated installed-wheel `python -I fofoca-local/tests/wheel_contract.py` passed 25 tests: all 21 original engine tests reused unchanged plus 4 local composition/data/security tests. Exactly one native extension is installed. Separate isolated old-first and new-first import checks both passed with native/class identity.
- `openspec validate add-fofoca-local --strict` and `git diff --check` passed. Engine settlement/session/domain source and original tests have no diff; `fofoca-data/` and `fofoca-api/` have no diff. Tests used loopback mock servers only; no database, real API, schema, data or deployment operations occurred.
- Existing unrelated untracked directories were left untouched. The workspace lockfile is supplied for source control, but no commit or push was made (not authorized). Cargo emitted only the pre-existing user-config deprecation warning; global configuration was not changed.
