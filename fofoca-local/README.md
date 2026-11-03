# fofoca-local

One CPython **3.12** maturin distribution for explicit read-only API access and
the existing Rust-owned daily backtest session. No database, backfill, automatic
environment/dotenv loading, scheduler, cache, or strategy runner is included.

## Workspace and compatibility

`Cargo.toml` owns dependency versions/features and the single authoritative
`Cargo.lock` (include it in source control). `data/` is the blocking reqwest client;
the engine member is `../fofoca-engine`, intentionally retained in place to reuse
all authoritative source and tests without copying them. The workspace root is
the only native composition crate. The default engine dependency graph remains
free of HTTP, serde and Python dependencies. Reqwest blocking internally uses its
own runtime; callers do not need an async runtime.

The supported build entry point is this directory's `pyproject.toml`, **not** the
old engine directory. The `fofoca-local` wheel includes `fofoca_local.data`,
`fofoca_local.engine` and a `fofoca_engine` compatibility shim. Both engine imports
export the **identical** `BacktestSession`, types and exceptions; the private
`fofoca_engine._native` import aliases `fofoca_local._native` in `sys.modules`.
Exception/native session module identities retain `fofoca_engine._native` for
compatibility. There is only one native extension and one settlement implementation.
Do not co-install the obsolete `fofoca-engine` distribution: uninstall it before
installing this wheel, as it owns the same compatibility package paths.

## Build, install and validate (repository root)

Use fresh temporary directories if these names already exist. Build and test
venvs are separate; never install into the system interpreter. Python 3.12 with
its development/link library is needed for ordinary all-feature Cargo tests.
PyO3 is pinned to 0.30.1; maturin supplies extension linking without the deprecated
`extension-module` feature. Wheels are CPython-version-specific, not abi3.

```sh
ls /tmp/opencode
python3.12 -m venv /tmp/opencode/fofoca-local-build-312
/tmp/opencode/fofoca-local-build-312/bin/python -m pip install 'maturin>=1.9.4,<2'
PYO3_PYTHON=python3.12 /tmp/opencode/fofoca-local-build-312/bin/maturin build --locked --release \
  --manifest-path fofoca-local/Cargo.toml \
  --interpreter /tmp/opencode/fofoca-local-build-312/bin/python \
  --out /tmp/opencode/fofoca-local-wheels-312
python3.12 -m venv /tmp/opencode/fofoca-local-test-312
/tmp/opencode/fofoca-local-test-312/bin/python -m pip install /tmp/opencode/fofoca-local-wheels-312/*.whl
/tmp/opencode/fofoca-local-test-312/bin/python -I fofoca-local/tests/wheel_contract.py

cargo fmt --manifest-path fofoca-local/Cargo.toml --all --check
cargo check --locked --manifest-path fofoca-local/Cargo.toml --workspace
cargo test --locked --manifest-path fofoca-local/Cargo.toml --workspace
cargo clippy --locked --manifest-path fofoca-local/Cargo.toml --workspace --all-targets -- -D warnings
PYO3_PYTHON=python3.12 cargo check --locked --manifest-path fofoca-local/Cargo.toml --workspace --all-features
PYO3_PYTHON=python3.12 cargo test --locked --manifest-path fofoca-local/Cargo.toml --workspace --all-features
PYO3_PYTHON=python3.12 cargo clippy --locked --manifest-path fofoca-local/Cargo.toml --workspace --all-targets --all-features -- -D warnings
openspec validate add-fofoca-local --strict
git diff --check
```

Tests only contact loopback mock HTTP servers. The wheel suite reuses the complete
original engine contract rather than duplicating it, checks compatibility class
identity, exact typed NAV records, explicit composition, error categories, timeout,
redirect protection and redacted diagnostics. No real service or DB is required.

## Explicit data API

```python
from datetime import date
from fofoca_local import data, engine

# Supply credentials explicitly from your own trusted secret mechanism.
# Never put real passwords in source control, logs or a URL.
config = data.Config(
    base_url="https://nav.example.test",
    username="example-reader",
    password="replace-through-your-secret-mechanism",
    connect_timeout=2.0,
    timeout=10.0,
)
history = data.fetch_nav_history(
    "000001", date(2026, 1, 1), date(2026, 1, 5), config=config,
)
session = engine.BacktestSession()
session.initialize(history.start, history.end, "100")
prices = {row.nav_date: row.unit_nav for row in history.rows}
while session.account()["status"] != "finished":
    day = session.account()["current_date"]
    # Caller explicitly chooses daily orders. Missing NAV is not substituted.
    outcome = session.advance_day([], {history.code: prices.get(day)})
    assert outcome["status"] == "advanced"
```

The URL must be an absolute HTTP(S) origin (only `/`, no prefix/query/fragment or
URL credentials); HTTPS is required for credentials over untrusted networks.
HTTP is supported for loopback tests/trusted local deployments. Username/password
must be nonempty without control characters; username cannot contain `:`.
Timeouts are explicit seconds, with `0 < connect_timeout <= timeout <= 300`.
No environment variables, `.env`, proxy settings, or implicit credentials are read.
All redirects, even same-origin, are rejected as HTTP errors, so credentials are
never forwarded. Configure the final API origin directly. There are no retries.

`fetch_nav_history(code, start, end, *, config)` accepts a six-ASCII-digit code and
`datetime.date` endpoints (not datetime), sends only authenticated
`GET /funds/{code}/nav?start=...&end=...`, and returns an immutable `History`:
`code`, `start`, `end`, `rows: tuple[NavRow, ...]`. Each row has `nav_date: date`,
`unit_nav: str | None`, and `accumulated_nav: str | None`. Decimal strings retain
their original spelling/precision; no float conversion, date filling or accumulated
NAV substitution occurs. Empty rows mean only that no stored rows were returned,
not proof of complete coverage. The data client accepts exact decimal strings;
the engine's existing positive NAV/scale limits still apply when callers trade.

HTTP status must be exactly 200; identity/range must match the request. Dates must
be canonical ISO dates, within the inclusive range and strictly ascending (no
duplicates). Both metric fields must be present with string or explicit null values;
present strings must be plain decimal numbers. The entire body is validated before
returning any records. Python exposes `ConfigurationError`, `InputError`,
`TransportError` (including bounded timeouts), `HttpError`, `ResponseError`, all
`RuntimeError` subclasses. Wrong Python argument types raise `TypeError`.
Rust exposes the corresponding `fofoca_local_data::Error` variants. Config repr
and all client error Display/Debug messages are redacted: upstream error bodies,
URLs, headers and transport sources are deliberately not included.

For the unchanged account, rejection, exact-value and daily settlement semantics,
see [the engine contract](../fofoca-engine/README.md).
