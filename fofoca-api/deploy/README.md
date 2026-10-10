# fofoca-api deployment and testing

This document describes how to deploy and validate `fofoca-api` safely.
The service is read-only against the existing FOFoca PostgreSQL schema;
deployment never changes data or schema, and rollback never requires a
data migration. All operations are manual: this document does not
introduce timers, schedulers, or automatic restart policies, and it does
not authorize changes to production or Ariadne resources.

## 1. Provision the dedicated read-only role

The API must run with its own least-privilege role on the **dedicated
FOFoca database** — never the Python writer's credentials and never
Ariadne's database. As a database administrator on the FOFoca cluster:

```sql
CREATE ROLE fofoca_api_ro LOGIN PASSWORD '<externally generated>';

-- On the FOFoca database:
GRANT CONNECT ON DATABASE fofoca TO fofoca_api_ro;
GRANT USAGE ON SCHEMA public TO fofoca_api_ro;
GRANT SELECT ON TABLE fund TO fofoca_api_ro;
GRANT SELECT ON TABLE fund_nav_daily TO fofoca_api_ro;
```

Do not grant ownership, INSERT/UPDATE/DELETE, TRUNCATE, DDL, sequence, or
schema-creation privileges. Audit effective privileges, not just direct
grants: role memberships and `PUBLIC` privileges can also grant access.
In particular, check database `TEMP` and schema `CREATE` privileges. A DBA
must arrange the dedicated database's privilege baseline so this role has
only the required access; do not blindly revoke shared `PUBLIC` privileges
on an existing production database, since that can affect other users.
The application additionally declares
`default_transaction_read_only=on` per connection as defense in depth,
but the grants above are the real protection. The application performs
no schema initialization or migration at startup; the schema already
exists from the Python application's `init-db`.

## 2. Inject configuration externally

Secrets are injected by the environment and are never committed. The
application does not load `.env` files by itself; if an operator sources
an env file, that is an explicit shell action, not application behavior.

| Variable | Notes |
| --- | --- |
| `FOFOCA_DATABASE_URL` | SQLx URL syntax, e.g. `postgresql://fofoca_api_ro:<password>@127.0.0.1:5432/fofoca`. libpq keyword DSNs are rejected. |
| `FOFOCA_API_BASIC_USERNAME` | One externally rotated account; no colon/control characters. |
| `FOFOCA_API_BASIC_PASSWORD` | Verbatim password; no control characters; colons allowed. |
| `FOFOCA_API_BIND` | Optional; defaults to `127.0.0.1:8080`. Keep it local behind the proxy. |
| `FOFOCA_API_MAX_RANGE_DAYS` | Optional; defaults to `3660`. Positive integers only. Larger ceilings increase worst-case response memory; keep it modest because results are bounded by the requested span for one code. |

Startup fails closed (before listening) on missing/invalid settings or an
unreachable database, and diagnostics never echo supplied values.

Only `postgres`/`postgresql` URL schemes are accepted. SQLx's default
connection fallbacks remain enabled: omitted URL fields can use `PG*`
environment settings, and an absent password can use `PGPASSFILE` or the
service user's home `.pgpass`. A passwordless local connection is valid;
the application does not require a password or alter process environment.
Run under a dedicated service account with an isolated environment/home.
Remove unintended PG settings (including host/user/database/password,
TLS/session options and passfile settings), and exclude unrelated home
credential files. If fallbacks are needed, explicitly provision only
dedicated FOFoca read-only values and a protected passfile. Do not inherit
an administrator's, Python writer's, Ariadne's, or production operator's
PG environment/home. This is SQLx compatibility, not auto-dotenv loading.

## 3. Build

```sh
cargo build --locked --release --manifest-path fofoca-api/Cargo.toml
```

The committed `Cargo.lock` makes the resolution reproducible. No database
or environment is required at build time (runtime SQL, no compile-time
query macros).

## 4. Run behind a TLS-terminating reverse proxy

Remote production traffic must use HTTPS. Terminate TLS at a reverse
proxy and forward to the locally bound HTTP upstream. Basic credentials
are reusable and base64 is not encryption; never expose the plain-HTTP
listener beyond localhost in production.

Example nginx sketch (adapt to your site; not a managed unit file):

Configure proxy logs separately to omit Authorization, credentials, and
raw request URIs/query strings. Default access-log formats can record query
strings; the application's safe logging does not sanitize proxy logs.

```nginx
server {
    listen 443 ssl;
    server_name funds.example.internal;
    # ssl_certificate / ssl_certificate_key configured by the operator.

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        # The Authorization header is forwarded unchanged; the API
        # authenticates every path, including unknown ones.
    }
}
```

Start the service manually with the environment injected by your process
supervision of choice; this document prescribes no auto-restart policy:

```sh
FOFOCA_DATABASE_URL=... FOFOCA_API_BASIC_USERNAME=... FOFOCA_API_BASIC_PASSWORD=... \
  ./fofoca-api
```

## 5. Smoke-test locally (plain HTTP is fine on localhost)

```sh
curl -i 'http://127.0.0.1:8080/funds/166009/nav?start=2026-09-23&end=2026-09-25'
# -> 401 with WWW-Authenticate: Basic realm="fofoca-api", charset="UTF-8"

curl -u "$FOFOCA_API_BASIC_USERNAME:$FOFOCA_API_BASIC_PASSWORD" \
  'http://127.0.0.1:8080/funds/166009/nav?start=2026-09-23&end=2026-09-25'
# -> 200 {"code":"166009",...,"rows":[...]}
```

## 6. Testing

Unit and HTTP-contract tests need no database:

```sh
cargo test --locked --manifest-path fofoca-api/Cargo.toml
```

The disposable-PostgreSQL integration suite is separately gated and
isolated:

```sh
fofoca-api/scripts/run_integration_tests.sh
```

- Requires `initdb`, `pg_ctl`, `psql`, `pg_isready` on `PATH`; with the
  gate enabled, missing tools are a failure, not a silent skip.
- The script creates a private `/tmp/fofoca-api-suite-XXXXXX` sandbox,
  launches Cargo with an empty environment and private HOME/TMPDIR (while
  preserving PATH and Cargo/Rustup tool locations), and sets the integration
  gate explicitly. This excludes ambient PG settings, passfiles and `.pgpass`.
- Database tests create unique `fofoca-api-it-*` child clusters under that
  sandbox with a
  unique localhost port, loads fixtures under the bootstrap owner, runs
  the API under the read-only role, and verifies mutation/schema-creation
  denial plus unchanged schema/data fingerprints.
- Cleanup runs on normal completion and Rust panic unwinding. It cannot
  guarantee cleanup after `SIGKILL` or a host failure. Stop failures must
  retain the directory for diagnosis rather than deleting a live cluster's
  files; inspect the reported directory and stop that specific cluster
  before removing it. The script fails if its own cluster resources leak,
  and never scans or cleans another run's directories. The empty sandbox
  is removed only after successful cleanup; diagnostics are retained on
  cluster-stop failure.
- The suite cannot target Ariadne or production: it only ever starts its
  own throwaway clusters, and all connection URLs it builds point at
  `127.0.0.1` on its own ports. Never export a production
  `FOFOCA_DATABASE_URL` when running tests; the script's empty environment
  excludes ambient FOFoca and PG variables before running.

## 7. Rollback

Rollback stops and removes the API process and its reverse-proxy
exposure. No data or schema migration is involved: the Python
application, its tables, and its backfill behavior are unchanged by this
service's presence or removal. The read-only role may be dropped by a DBA
as a separate explicit step.
