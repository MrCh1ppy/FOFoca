# fofoca-engine (binding skeleton)

Independent edition-2024 Rust library with a **PyO3 extension and thin typed Python
facade** as the primary entry point. No HTTP, SQLite, persistence, data fetching,
environment credentials, deployment, or scheduling. Existing applications are
unchanged. A future local Rust application can directly link this library and
compose data/report components without Python or a subprocess.

## Current scope (not a complete backtest engine)

`BacktestSession()` constructs a distinct native, empty session. `initialize`
accepts `datetime.date`, `datetime.date`, and decimal **str**, but always raises
`NotImplementedError`; it does not retain configuration. `account`, `set_orders`,
`advance_day`, and `report` raise `fofoca_engine.NotInitializedError` (a native
module exception derived from `RuntimeError`). Reports never fabricate success.
No second Python state, JSON round trip, subprocess, or Tokio runtime is involved.

The Python wrapper provides inline typing, `Order`/`Market` TypedDicts, a private
extension stub, and `py.typed`. Native checks currently cover argument count and
outer input types only. Nested order/market and business date/code/decimal
semantics, precision, successful account initialization, trading, settlement,
valuation, and report output remain unimplemented. Return annotations are `None`
for this rejecting skeleton, not a promise about future account/report types.
Float input is unsupported; no float precision contract is made. Typed Rust
domain configuration/output types will be introduced with their business phase,
not invented as Python objects or wire JSON in the core. The current core
`Session::initialize()` is deliberately argument-free and always rejects.

```python
from datetime import date
from fofoca_engine import BacktestSession, NotInitializedError

session = BacktestSession()
try:
    session.initialize(date(2026, 1, 1), date(2026, 1, 3), "100")
except NotImplementedError:
    pass  # Expected: no account was created.
try:
    session.account()
except NotInitializedError:
    pass
```

## Packaging and real-wheel validation (repository root)

PyO3 is pinned to stable **0.30.1**. CPython **3.12** is the sole currently
supported and validated baseline (`requires-python = ">=3.12,<3.13"`). Maturin
>=1.9.4,<2 handles extension linking; do **not** enable PyO3's deprecated
`extension-module` feature. Build version-specific, non-abi3 wheels; a wheel is
not promised to import under another interpreter version. Build tools and test
installation live in separate temporary venvs, never the system interpreter.
Use fresh directories if these example names are already occupied.

`fofoca-engine/.python-version` (`3.12`) is a hint for version managers only.
Cargo/PyO3 does not read it, so every Python-feature command below sets
`PYO3_PYTHON=python3.12` explicitly.

```sh
ls /tmp/opencode
python3.12 -m venv /tmp/opencode/fofoca-engine-build-312
/tmp/opencode/fofoca-engine-build-312/bin/python -m pip install 'maturin>=1.9.4,<2'
PYO3_PYTHON=python3.12 /tmp/opencode/fofoca-engine-build-312/bin/maturin build --locked --release \
  --manifest-path fofoca-engine/Cargo.toml \
  --interpreter /tmp/opencode/fofoca-engine-build-312/bin/python \
  --out /tmp/opencode/fofoca-engine-wheels-312
python3.12 -m venv /tmp/opencode/fofoca-engine-test-312
/tmp/opencode/fofoca-engine-test-312/bin/python -m pip install --force-reinstall \
  /tmp/opencode/fofoca-engine-wheels-312/*.whl
/tmp/opencode/fofoca-engine-test-312/bin/python -I fofoca-engine/tests/wheel_contract.py
```

The installed-wheel suite checks native import, independent objects, actual
exceptions and type failures, typing artifacts, repeated failed initialization,
and every exposed business operation. An audit hook rejects child-process
operations before import and PATH is empty throughout. No test replaces the
extension or engine with mocks. The wheel contains `fofoca_engine._native` and
`python/fofoca_engine/`. Historical note: an earlier cp314 wheel was built and
its eight tests passed on this host; because the wheel is version-specific and
non-abi3, that result is retained only as history and does not promise that the
cp312 wheel imports under 3.14.

## Rust features and validation (repository root)

- Default features are empty: synchronous Rust-only `Session` and module-local
  SNAFU `SessionError`; no dependency on Python, Tokio, or wire/JSON types.
- `python` enables only the private PyO3 adapter. Normal cargo tests link Python
  normally and need its development/link library; maturin supplies extension-specific
  linking for wheel builds. CPython 3.12 is the supported baseline and provides the
  link library used by the Python-feature commands below. Historical (not a current
  support claim): an earlier attempt with this host's system 3.14.7 failed ordinary
  Python-feature cargo test with `unable to find library -lpython3.14`, although its
  wheel built and imported successfully.
- `cli` enables optional Tokio (macros/rt/io-std/io-util), serde/JSON, CLI library
  facade `run`/`CliError`, binary, and process regression test. It uses the **same**
  core session and no parallel tasks, Arc/Mutex, or separate business state.
- SNAFU errors belong to their actual modules (session, protocol, cli). The Python
  adapter only converts errors to Python exceptions; it needs no error enum.
- Chrono and rust_decimal remain the date/exact-arithmetic dependencies for later
  authorized business work; the documentation-only domain modules do no work yet.

```sh
cargo fmt --check --manifest-path fofoca-engine/Cargo.toml
cargo check --locked --manifest-path fofoca-engine/Cargo.toml
cargo test --locked --manifest-path fofoca-engine/Cargo.toml
cargo clippy --locked --manifest-path fofoca-engine/Cargo.toml --all-targets -- -D warnings
cargo check --locked --manifest-path fofoca-engine/Cargo.toml --features cli
cargo test --locked --manifest-path fofoca-engine/Cargo.toml --features cli
cargo clippy --locked --manifest-path fofoca-engine/Cargo.toml --features cli --all-targets -- -D warnings
PYO3_PYTHON=python3.12 cargo check --locked --manifest-path fofoca-engine/Cargo.toml --features python
PYO3_PYTHON=python3.12 cargo test --locked --manifest-path fofoca-engine/Cargo.toml --features python
PYO3_PYTHON=python3.12 cargo clippy --locked --manifest-path fofoca-engine/Cargo.toml --features python --all-targets -- -D warnings
PYO3_PYTHON=python3.12 cargo check --locked --manifest-path fofoca-engine/Cargo.toml --all-features
PYO3_PYTHON=python3.12 cargo test --locked --manifest-path fofoca-engine/Cargo.toml --all-features
PYO3_PYTHON=python3.12 cargo clippy --locked --manifest-path fofoca-engine/Cargo.toml --all-features --all-targets -- -D warnings
```

## Optional secondary CLI

```sh
cargo run --locked --manifest-path fofoca-engine/Cargo.toml --features cli --bin fofoca-engine
```

The original JSON Lines contract and fourteen real-process Python tests remain.
The Rust `tests/process.rs` regression invokes `python3.12` explicitly rather than
the system `python3`, so CLI tests cannot silently use 3.14.
Only `quit` succeeds with `{}` and flushes before zero exit. Shape-valid `init`
returns `not_implemented`; other business commands return `not_initialized`.
Every response has `state: null`, even after repeated init. Request IDs correlate
only, not idempotency. Unknown fields and recursively duplicated keys fail.
Invalid/duplicate JSON has null response ID; otherwise a valid ID is recovered.
Each line, including a blank line or nonempty final unterminated line, receives
one response and flush. Empty EOF exits silently. Quit ignores subsequent input.
UTF-8/I/O failures exit nonzero with stderr-only diagnostics. No automatic close,
settlement, export, or recovery occurs on quit/EOF or Python object disposal.
The optional CLI is not a required runtime component of the Python package.

Business rules in OpenSpec remain the later target: exact bounded decimals,
natural-day D+1 settlement, no fees/distributions, trusted-author data/strategy,
fixed candidate versus committed market, stale valuation, and terminal pending
assets retained without liquidation. None is claimed implemented here.
