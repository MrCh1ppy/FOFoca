//! Isolated disposable-PostgreSQL integration tests for the read path.
//!
//! This harness is API-specific: it creates a unique local cluster under
//! `/tmp/fofoca-api-it-*` with a unique port and socket directory, never
//! reuses the Python `exec pytest` harness, and never targets Ariadne or
//! production resources. Cleanup runs on success and failure via `Drop`:
//! the cluster is stopped with `pg_ctl -m immediate` and its data
//! directory removed only after a confirmed stop. Failed cleanup retains diagnostics.
//!
//! **Gating:** these tests only run when `FOFOCA_API_INTEGRATION=1` is set
//! (e.g. via `scripts/run_integration_tests.sh`). A plain `cargo test`
//! never starts any database. When the gate is enabled but PostgreSQL
//! server tools (`initdb`, `pg_ctl`, `psql`, `pg_isready`) are missing,
//! tests FAIL rather than silently skip — a gated run without tools is a
//! broken environment, not a pass.
//!
//! Fixture setup runs as the bootstrap superuser; the API queries under a
//! separate read-only role with only CONNECT / schema USAGE / SELECT on
//! the two existing tables.

use std::net::TcpListener;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tracing::instrument::WithSubscriber as _;

use axum::http::{Method, StatusCode};
use axum::response::Response;
use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as B64;
use http::Request;
use http::header::AUTHORIZATION;
use tower::ServiceExt as _;

use fofoca_api::{Config, HttpService};

const API_USER: &str = "apitest";
const API_PASS: &str = "int3gration:pass";
const RO_ROLE: &str = "fofoca_api_ro";
const RO_PASS: &str = "readonly-local-only";
const DB_NAME: &str = "fofoca_it";

/// Environment variable that enables this disposable-database suite.
const GATE_ENV: &str = "FOFOCA_API_INTEGRATION";

// libpq environment and user credential files must never redirect a test command.
fn pg_command(tool: &str) -> Command {
    let mut command = Command::new(tool);
    for (key, _) in std::env::vars_os() {
        if key.to_string_lossy().starts_with("PG") {
            command.env_remove(key);
        }
    }
    command
        .env("PGPASSFILE", "/dev/null")
        .env("PGSERVICEFILE", "/dev/null");
    command
}

/// Whether the suite is enabled for this process.
fn gated() -> bool {
    std::env::var(GATE_ENV).map(|v| v == "1").unwrap_or(false)
}

fn tools_available() -> bool {
    ["initdb", "pg_ctl", "psql", "pg_isready"]
        .iter()
        .all(|tool| {
            pg_command(tool)
                .arg("--version")
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .map(|s| s.success())
                .unwrap_or(false)
        })
}

/// Gate check shared by every test: skip ungated runs, fail gated runs
/// without the required server tools.
fn require_gate(test: &str) -> bool {
    if !gated() {
        eprintln!("skipping {test}: set {GATE_ENV}=1 to run disposable-PostgreSQL tests");
        return false;
    }
    assert!(
        tools_available(),
        "{test}: {GATE_ENV}=1 requires initdb/pg_ctl/psql/pg_isready on PATH"
    );
    true
}

/// Pick a free localhost port and immediately release it.
fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}

/// A running disposable cluster; `Drop` stops and removes it.
struct Cluster {
    dir: PathBuf,
    port: u16,
}

impl Cluster {
    fn data_dir(&self) -> PathBuf {
        self.dir.join("data")
    }

    fn socket_dir(&self) -> PathBuf {
        self.dir.join("sock")
    }

    fn log_file(&self) -> PathBuf {
        self.dir.join("postgres.log")
    }

    /// Start a fresh isolated cluster owned by the current OS user.
    fn allocate() -> Cluster {
        let unique = format!(
            "fofoca-api-it-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let dir = std::env::temp_dir().join(unique);
        let port = free_port();
        std::fs::create_dir(&dir).unwrap();
        let cluster = Cluster { dir, port };
        std::fs::set_permissions(&cluster.dir, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::create_dir(cluster.socket_dir()).unwrap();
        cluster
    }

    fn start() -> Cluster {
        let cluster = Self::allocate();

        // initdb: trust auth on the local socket only; the cluster listens
        // on a unique localhost port and is discarded afterwards.
        run_ok(
            pg_command("initdb")
                .args([
                    "-D",
                    cluster.data_dir().to_str().unwrap(),
                    "-A",
                    "trust",
                    "-U",
                    "postgres",
                    "--no-sync",
                ])
                .stdout(Stdio::null())
                .stderr(Stdio::null()),
            "initdb",
        );

        cluster.pg_ctl(&[
            "start",
            "-w",
            "-t",
            "30",
            "-o",
            &format!(
                "-p {} -k {} -c listen_addresses=127.0.0.1 -c logging_collector=off",
                cluster.port,
                cluster.socket_dir().display()
            ),
            "-l",
            cluster.log_file().to_str().unwrap(),
        ]);
        cluster.wait_ready();
        cluster
    }

    fn pg_ctl(&self, args: &[&str]) {
        run_ok(
            pg_command("pg_ctl")
                .arg("-D")
                .arg(self.data_dir())
                .args(args)
                .stdout(Stdio::null())
                .stderr(Stdio::null()),
            "pg_ctl",
        );
    }

    fn wait_ready(&self) {
        let deadline = Instant::now() + Duration::from_secs(30);
        loop {
            let ok = pg_command("pg_isready")
                .args([
                    "-h",
                    self.socket_dir().to_str().unwrap(),
                    "-p",
                    &self.port.to_string(),
                    "-U",
                    "postgres",
                ])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .map(|s| s.success())
                .unwrap_or(false);
            if ok {
                return;
            }
            assert!(
                Instant::now() < deadline,
                "disposable cluster did not start"
            );
            std::thread::sleep(Duration::from_millis(100));
        }
    }

    /// Run SQL against the bootstrap superuser, panicking on failure.
    fn psql(&self, database: &str, sql: &str) {
        run_ok(
            pg_command("psql")
                .args([
                    "-h",
                    self.socket_dir().to_str().unwrap(),
                    "-p",
                    &self.port.to_string(),
                    "-U",
                    "postgres",
                    "-d",
                    database,
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-X",
                    "-q",
                    "-c",
                    sql,
                ])
                .stdout(Stdio::null())
                .stderr(Stdio::null()),
            "psql",
        );
    }

    /// URL for the read-only API role.
    fn ro_url(&self) -> String {
        format!(
            "postgresql://{RO_ROLE}:{RO_PASS}@127.0.0.1:{}/{DB_NAME}",
            self.port
        )
    }

    fn cleanup(&self, stop_tool: &str) -> Result<(), String> {
        if self.data_dir().join("postmaster.pid").exists() {
            let output = pg_command(stop_tool)
                .arg("-D")
                .arg(self.data_dir())
                .args(["stop", "-m", "immediate", "-w", "-t", "10"])
                .output()
                .map_err(|e| format!("stop failed: {e}; retained {}", self.dir.display()))?;
            if !output.status.success() {
                return Err(format!(
                    "stop failed ({}): {}; retained {} (postgres.log)",
                    output.status,
                    String::from_utf8_lossy(&output.stderr),
                    self.dir.display()
                ));
            }
        }
        std::fs::remove_dir_all(&self.dir)
            .map_err(|e| format!("cleanup failed: {e}; retained {}", self.dir.display()))
    }
}

impl Drop for Cluster {
    fn drop(&mut self) {
        if !self.dir.exists() {
            return;
        }
        if let Err(error) = self.cleanup("pg_ctl") {
            eprintln!("ERROR: {error}");
            if !std::thread::panicking() {
                panic!("{error}");
            }
        }
    }
}

fn run_ok(command: &mut Command, what: &str) {
    let status = command
        .status()
        .unwrap_or_else(|e| panic!("failed to spawn {what}: {e}"));
    assert!(status.success(), "{what} exited with {status}");
}

/// Create the existing two-table schema verbatim (mirrors the Python
/// bundled DDL; tests read the same shape, they do not modify Python).
fn create_schema(cluster: &Cluster) {
    cluster.psql("postgres", &format!("CREATE DATABASE {DB_NAME}"));
    cluster.psql(
        DB_NAME,
        "CREATE TABLE fund (
            id BIGSERIAL PRIMARY KEY,
            code VARCHAR(16) NOT NULL UNIQUE,
            name VARCHAR(128) NOT NULL,
            fund_type VARCHAR(64) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
            established_date DATE NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE fund_nav_daily (
            fund_id BIGINT NOT NULL REFERENCES fund(id),
            nav_date DATE NOT NULL,
            unit_nav NUMERIC(18,6) NULL,
            accumulated_nav NUMERIC(18,6) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (fund_id, nav_date)
        );",
    );
}

/// Provision the least-privilege API role: CONNECT, schema USAGE, SELECT.
fn create_readonly_role(cluster: &Cluster) {
    cluster.psql(
        "postgres",
        &format!("CREATE ROLE {RO_ROLE} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{RO_PASS}'"),
    );
    cluster.psql(
        DB_NAME,
        &format!(
            "REVOKE ALL ON DATABASE {DB_NAME} FROM PUBLIC;
             REVOKE ALL ON SCHEMA public FROM PUBLIC;
             GRANT CONNECT ON DATABASE {DB_NAME} TO {RO_ROLE};
             GRANT USAGE ON SCHEMA public TO {RO_ROLE};
             GRANT SELECT ON TABLE fund TO {RO_ROLE};
              GRANT SELECT ON TABLE fund_nav_daily TO {RO_ROLE};"
        ),
    );
}

/// Insert fixture funds and NAV rows as the bootstrap owner.
fn load_fixtures(cluster: &Cluster) {
    cluster.psql(
        DB_NAME,
        "INSERT INTO fund (code, name, fund_type) VALUES
            ('166009', 'EU Fund', '混合型'),
            ('000001', 'Leading Zero Fund', '股票型'),
            ('999999', 'Empty Fund', '债券型');
         INSERT INTO fund_nav_daily (fund_id, nav_date, unit_nav, accumulated_nav)
         SELECT f.id, d.nav_date, d.unit_nav, d.accumulated_nav
         FROM fund f JOIN (VALUES
            ('166009', DATE '2026-09-22', 1.000001::NUMERIC(18,6), 2.100000::NUMERIC(18,6)),
            ('166009', DATE '2026-09-23', 1.234567::NUMERIC(18,6), NULL),
            -- gap: 2026-09-24 intentionally missing
            ('166009', DATE '2026-09-25', NULL, 2.345678::NUMERIC(18,6)),
            ('166009', DATE '2026-09-26', 999999999999.123456::NUMERIC(18,6), 1.000000::NUMERIC(18,6)),
            ('000001', DATE '2026-09-23', 0.500000::NUMERIC(18,6), 0.600000::NUMERIC(18,6))
         ) AS d(code, nav_date, unit_nav, accumulated_nav) ON d.code = f.code;",
    );
}

/// Build the app router against the disposable cluster with the RO role.
async fn build_app(cluster: &Cluster) -> HttpService {
    let config = Config::load_for_integration_test(
        &cluster.ro_url(),
        API_USER,
        API_PASS,
        "127.0.0.1:8080",
        3660,
    );
    fofoca_api::router_for_integration_test(config)
        .await
        .expect("integration router must build against the disposable cluster")
}

fn auth_header() -> String {
    format!("Basic {}", B64.encode(format!("{API_USER}:{API_PASS}")))
}

async fn get(app: &HttpService, uri: &str, auth: Option<&str>) -> Response {
    let mut builder = Request::builder().method(Method::GET).uri(uri);
    if let Some(value) = auth {
        builder = builder.header(AUTHORIZATION, value);
    }
    app.clone()
        .oneshot(builder.body(axum::body::Body::empty()).unwrap())
        .await
        .unwrap()
}

async fn body_string(response: Response) -> String {
    let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .unwrap();
    String::from_utf8(bytes.to_vec()).unwrap()
}

macro_rules! it {
    ($name:ident, |$cluster:ident : Cluster| $body:block) => {
        #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
        async fn $name() {
            if !require_gate(stringify!($name)) {
                return;
            }
            let cluster_value = Cluster::start();
            create_schema(&cluster_value);
            create_readonly_role(&cluster_value);
            load_fixtures(&cluster_value);
            let $cluster: &Cluster = &cluster_value;
            $body
        }
    };
}

// ---------------------------------------------------------------------------
// Stored-data semantics (task 3.5)
// ---------------------------------------------------------------------------

it!(
    inclusive_bounds_ordering_precision_nulls,
    |cluster: Cluster| {
        let app = build_app(cluster).await;
        let response = get(
            &app,
            "/funds/166009/nav?start=2026-09-23&end=2026-09-25",
            Some(&auth_header()),
        )
        .await;
        assert_eq!(response.status(), StatusCode::OK);
        let body = body_string(response).await;
        let json: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(json["code"], "166009");
        assert_eq!(json["start"], "2026-09-23");
        assert_eq!(json["end"], "2026-09-25");
        let rows = json["rows"].as_array().unwrap();
        // Inclusive bounds: 09-22 excluded, gap 09-24 not synthesized.
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["nav_date"], "2026-09-23");
        assert_eq!(rows[0]["unit_nav"], "1.234567");
        assert_eq!(rows[0]["accumulated_nav"], serde_json::Value::Null);
        assert_eq!(rows[1]["nav_date"], "2026-09-25");
        assert_eq!(rows[1]["unit_nav"], serde_json::Value::Null);
        // Accumulated NAV stays in its own field; no substitution.
        assert_eq!(rows[1]["accumulated_nav"], "2.345678");
    }
);

it!(max_precision_and_trailing_zeros, |cluster: Cluster| {
    let app = build_app(cluster).await;
    let response = get(
        &app,
        "/funds/166009/nav?start=2026-09-26&end=2026-09-26",
        Some(&auth_header()),
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);
    let json: serde_json::Value = serde_json::from_str(&body_string(response).await).unwrap();
    let rows = json["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 1);
    // Full NUMERIC(18,6) precision survives as an exact string.
    assert_eq!(rows[0]["unit_nav"], "999999999999.123456");
    // Trailing zeros are preserved at six fractional digits.
    assert_eq!(rows[0]["accumulated_nav"], "1.000000");
});

it!(
    leading_zero_code_and_unknown_and_empty,
    |cluster: Cluster| {
        let app = build_app(cluster).await;
        // Leading-zero code is looked up verbatim.
        let response = get(
            &app,
            "/funds/000001/nav?start=2026-09-23&end=2026-09-23",
            Some(&auth_header()),
        )
        .await;
        assert_eq!(response.status(), StatusCode::OK);
        let json: serde_json::Value = serde_json::from_str(&body_string(response).await).unwrap();
        assert_eq!(json["rows"].as_array().unwrap().len(), 1);
        assert_eq!(json["rows"][0]["unit_nav"], "0.500000");

        // Unknown code: 200 with empty rows.
        let response = get(
            &app,
            "/funds/123456/nav?start=2026-09-23&end=2026-09-24",
            Some(&auth_header()),
        )
        .await;
        assert_eq!(response.status(), StatusCode::OK);
        let json: serde_json::Value = serde_json::from_str(&body_string(response).await).unwrap();
        assert_eq!(json["rows"].as_array().unwrap().len(), 0);
        assert_eq!(json["code"], "123456");

        // Fund without NAV rows: 200 with empty rows.
        let response = get(
            &app,
            "/funds/999999/nav?start=2026-09-23&end=2026-09-24",
            Some(&auth_header()),
        )
        .await;
        assert_eq!(response.status(), StatusCode::OK);
        let json: serde_json::Value = serde_json::from_str(&body_string(response).await).unwrap();
        assert_eq!(json["rows"].as_array().unwrap().len(), 0);

        // Empty interval (no stored rows in range): 200 with empty rows.
        let response = get(
            &app,
            "/funds/166009/nav?start=2020-01-01&end=2020-01-02",
            Some(&auth_header()),
        )
        .await;
        assert_eq!(response.status(), StatusCode::OK);
        let json: serde_json::Value = serde_json::from_str(&body_string(response).await).unwrap();
        assert_eq!(json["rows"].as_array().unwrap().len(), 0);
    }
);

it!(stored_funds_regardless_of_status, |cluster: Cluster| {
    // A fund whose status is not ACTIVE is still stored history: the API
    // does not reapply backfill eligibility.
    cluster.psql(
        DB_NAME,
        "UPDATE fund SET status = 'SUSPENDED' WHERE code = '166009'",
    );
    let app = build_app(cluster).await;
    let response = get(
        &app,
        "/funds/166009/nav?start=2026-09-23&end=2026-09-23",
        Some(&auth_header()),
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);
    let json: serde_json::Value = serde_json::from_str(&body_string(response).await).unwrap();
    assert_eq!(json["rows"].as_array().unwrap().len(), 1);
});

// ---------------------------------------------------------------------------
// Read-only role enforcement and schema/data invariance (task 3.4)
// ---------------------------------------------------------------------------

it!(readonly_role_cannot_mutate_or_create, |cluster: Cluster| {
    // Attempt mutation/schema creation directly as the RO role: denied.
    let url = cluster.ro_url();
    let pool = sqlx::PgPool::connect(&url).await.unwrap();
    let mut conn = pool.acquire().await.unwrap();
    sqlx::query("SET default_transaction_read_only = off")
        .execute(&mut *conn)
        .await
        .unwrap();
    for sql in [
        "INSERT INTO fund (code, name, fund_type) VALUES ('000000','x','y')",
        "INSERT INTO fund_nav_daily (fund_id, nav_date) VALUES (1, DATE '2026-01-01')",
        "UPDATE fund SET name = 'hacked'",
        "DELETE FROM fund",
        "UPDATE fund_nav_daily SET unit_nav = 0",
        "DELETE FROM fund_nav_daily",
        "TRUNCATE fund_nav_daily",
        "CREATE TABLE hacked (id int)",
        "CREATE TEMP TABLE hacked (id int)",
        "CREATE SCHEMA hacked",
        "SELECT nextval('fund_id_seq')",
        "ALTER TABLE fund ADD COLUMN hacked int",
    ] {
        let error = sqlx::query(sql)
            .execute(&mut *conn)
            .await
            .expect_err("grants must deny mutation");
        assert_eq!(
            error.as_database_error().unwrap().code().as_deref(),
            Some("42501"),
            "{sql}"
        );
    }
    let allowed: bool = sqlx::query_scalar("SELECT
        (SELECT NOT (rolsuper OR rolinherit OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)
         FROM pg_roles WHERE rolname=current_user) AND
        has_database_privilege(current_user, current_database(), 'CONNECT') AND
        NOT has_database_privilege(current_user, current_database(), 'CREATE,TEMP') AND
        has_schema_privilege(current_user, 'public', 'USAGE') AND
        NOT has_schema_privilege(current_user, 'public', 'CREATE') AND
        (SELECT bool_and(relowner <> (SELECT oid FROM pg_roles WHERE rolname = current_user)
          AND has_table_privilege(current_user, oid, 'SELECT')
          AND NOT has_table_privilege(current_user, oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))
         FROM pg_class WHERE oid IN ('fund'::regclass, 'fund_nav_daily'::regclass)) AND
        NOT has_sequence_privilege(current_user, 'fund_id_seq', 'USAGE,SELECT,UPDATE')")
        .fetch_one(&mut *conn).await.unwrap();
    assert!(allowed, "role has privileges beyond CONNECT/USAGE/SELECT");
    drop(conn);
    pool.close().await;

    // After API startup and requests, schema and data remain unchanged.
    let before = table_fingerprint(cluster);
    let app = build_app(cluster).await;
    let response = get(
        &app,
        "/funds/166009/nav?start=2026-09-22&end=2026-09-26",
        Some(&auth_header()),
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);
    let after = table_fingerprint(cluster);
    assert_eq!(before, after, "schema/data changed by API traffic");
});

/// Snapshot schema object names + row counts + a content checksum.
fn table_fingerprint(cluster: &Cluster) -> String {
    let output = pg_command("psql")
        .args([
            "-h", cluster.socket_dir().to_str().unwrap(),
            "-p", &cluster.port.to_string(),
            "-U", "postgres",
            "-d", DB_NAME,
            "-X", "-q", "-t", "-A",
            "-c",
            "SELECT concat_ws('|',
                (SELECT jsonb_agg(jsonb_build_array(relname, relkind, relowner, relacl) ORDER BY relname)::text
                   FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r','i','S')),
                (SELECT count(*)::text FROM fund),
                (SELECT count(*)::text FROM fund_nav_daily),
                (SELECT jsonb_agg(to_jsonb(c) ORDER BY table_name, ordinal_position)::text FROM information_schema.columns c WHERE table_schema='public'),
                (SELECT jsonb_agg(jsonb_build_array(attrelid,attnum,attacl) ORDER BY attrelid,attnum)::text FROM pg_attribute WHERE attrelid IN ('fund'::regclass,'fund_nav_daily'::regclass) AND attnum>0),
                (SELECT jsonb_agg(jsonb_build_array(conname, pg_get_constraintdef(oid)) ORDER BY conname)::text FROM pg_constraint WHERE connamespace='public'::regnamespace),
                (SELECT jsonb_agg(to_jsonb(g) ORDER BY grantee, table_name, privilege_type)::text FROM information_schema.role_table_grants g WHERE table_schema='public'),
                (SELECT jsonb_agg(jsonb_build_array(nspname,nspowner,nspacl))::text FROM pg_namespace WHERE nspname='public'),
                (SELECT jsonb_agg(jsonb_build_array(datname,datdba,datacl))::text FROM pg_database WHERE datname=current_database()),
                md5((SELECT jsonb_agg(to_jsonb(f) ORDER BY id)::text FROM fund f)),
                md5((SELECT jsonb_agg(to_jsonb(n) ORDER BY fund_id,nav_date)::text FROM fund_nav_daily n))
            );",
        ])
        .output()
        .unwrap();
    assert!(output.status.success());
    String::from_utf8(output.stdout).unwrap().trim().to_string()
}

// ---------------------------------------------------------------------------
// Failure modes (task 3.6)
// ---------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn startup_fails_safely_when_database_unavailable() {
    // A syntactically valid URL pointing at a closed local port must fail
    // startup (pool connect) with a sanitized internal error — before any
    // listener exists. No credentials or URL appear in the Display output.
    let port = free_port(); // Nothing listens here.
    let url = format!("postgresql://ro:SecretDbMarker@127.0.0.1:{port}/fofoca_it");
    let config =
        Config::load_for_integration_test(&url, API_USER, API_PASS, "127.0.0.1:8080", 3660);
    let result = fofoca_api::router_for_integration_test(config).await;
    let error = result.expect_err("startup must fail without a database");
    assert_eq!(error.category(), "internal_error");
    let rendered = error.to_string();
    assert!(!rendered.contains("SecretDbMarker"));
    assert!(!rendered.contains("postgresql://"));
}

it!(
    pool_bounds_and_statement_timeout_hold,
    |cluster: Cluster| {
        // The app's bounded pool: max 5 connections, 5s acquire timeout, 10s
        // per-connection statement timeout.
        let app_pool = fofoca_api::connect_pool_for_integration_test(&cluster.ro_url()).await;

        // Hold all five connections deterministically; no sleeps/races are
        // needed to prove the sixth acquisition reaches the five-second bound.
        let mut permits = Vec::new();
        for _ in 0..5 {
            permits.push(app_pool.acquire().await.unwrap());
        }
        assert_eq!(app_pool.size(), 5, "pool did not reach max connections");
        assert_eq!(app_pool.num_idle(), 0, "connections should all be busy");

        let start = Instant::now();
        let acquire = app_pool.acquire().await;
        let elapsed = start.elapsed();
        assert!(acquire.is_err(), "sixth acquire should have timed out");
        let err = acquire.err().unwrap();
        assert!(
            matches!(err, sqlx::Error::PoolTimedOut),
            "expected PoolTimedOut, got {err:?}"
        );
        assert!(
            elapsed >= Duration::from_secs(4) && elapsed <= Duration::from_secs(7),
            "acquire timeout out of bounds: {elapsed:?}"
        );

        drop(permits);

        // (2) Statement timeout: a 15s sleep must fail under the 10s
        // per-connection statement_timeout applied by after_connect.
        let start = Instant::now();
        let slow = sqlx::query("SELECT pg_sleep(15)").execute(&app_pool).await;
        let elapsed = start.elapsed();
        assert!(slow.is_err(), "15s statement must fail under 10s timeout");
        assert_eq!(
            slow.unwrap_err()
                .as_database_error()
                .unwrap()
                .code()
                .as_deref(),
            Some("57014")
        );
        assert!(
            elapsed >= Duration::from_secs(9) && elapsed <= Duration::from_secs(13),
            "statement timeout out of bounds: {elapsed:?}"
        );

        app_pool.close().await;
    }
);

it!(
    database_failure_is_500_never_empty_200,
    |cluster: Cluster| {
        // A valid authenticated query during an outage must return a generic
        // 500 — never a fabricated empty rows array.
        let app = build_app(cluster).await;
        cluster.pg_ctl(&["stop", "-m", "immediate", "-w", "-t", "10"]);

        let response = get(
            &app,
            "/funds/166009/nav?start=2026-09-23&end=2026-09-24",
            Some(&auth_header()),
        )
        .await;
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        let body = body_string(response).await;
        assert_eq!(body, "{\"error\":\"internal_error\"}");
        assert!(
            !body.contains("\"rows\":[]"),
            "outage disguised as empty 200"
        );
    }
);

it!(
    unauthenticated_and_invalid_requests_do_not_acquire_pool,
    |cluster: Cluster| {
        let pool = fofoca_api::connect_pool_for_integration_test(&cluster.ro_url()).await;
        let config = Config::load_for_integration_test(
            &cluster.ro_url(),
            API_USER,
            API_PASS,
            "127.0.0.1:8080",
            3660,
        );
        let app = fofoca_api::router_with_pool_for_integration_test(config, pool.clone());
        let mut held = Vec::new();
        for _ in 0..5 {
            held.push(pool.acquire().await.unwrap());
        }
        assert_eq!(pool.num_idle(), 0);
        let uri = "/funds/166009/nav?start=2026-09-23&end=2026-09-24";
        for (method, path, auth, status) in [
            (Method::GET, uri, false, StatusCode::UNAUTHORIZED),
            (
                Method::GET,
                "/funds/166009/nav?start=bad&end=worse",
                true,
                StatusCode::BAD_REQUEST,
            ),
            (Method::GET, "/unknown", true, StatusCode::NOT_FOUND),
            (Method::POST, uri, true, StatusCode::METHOD_NOT_ALLOWED),
            (Method::HEAD, uri, true, StatusCode::METHOD_NOT_ALLOWED),
        ] {
            let mut request = Request::builder().method(method).uri(path);
            if auth {
                request = request.header(AUTHORIZATION, auth_header());
            }
            let response = tokio::time::timeout(
                Duration::from_secs(1),
                app.clone()
                    .oneshot(request.body(axum::body::Body::empty()).unwrap()),
            )
            .await
            .expect("rejection attempted pool acquisition")
            .unwrap();
            assert_eq!(response.status(), status);
        }
        let start = Instant::now();
        let response =
            tokio::time::timeout(Duration::from_secs(6), get(&app, uri, Some(&auth_header())))
                .await
                .expect("HTTP pool acquire exceeded 5s bound");
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            body_string(response).await,
            "{\"error\":\"internal_error\"}"
        );
        assert!(start.elapsed() >= Duration::from_secs(4));
        drop(held);
        pool.close().await;
    }
);

// ---------------------------------------------------------------------------
// Credential-safe failures (task 3.6 continued)
// ---------------------------------------------------------------------------

it!(
    failure_responses_and_logs_carry_no_credentials,
    |cluster: Cluster| {
        const SOURCE_MARKER: &str = "SecretSqlSourceMarker";
        cluster.psql(
            DB_NAME,
            &format!(
                "ALTER TABLE fund_nav_daily RENAME TO original_nav;
            CREATE FUNCTION fail_nav() RETURNS SETOF original_nav LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION '{SOURCE_MARKER}'; END $$;
            CREATE VIEW fund_nav_daily AS SELECT * FROM fail_nav();
            GRANT SELECT ON fund_nav_daily TO {RO_ROLE};"
            ),
        );
        let pool = sqlx::PgPool::connect(&cluster.ro_url()).await.unwrap();
        let source = sqlx::query("SELECT * FROM fund_nav_daily")
            .execute(&pool)
            .await
            .unwrap_err();
        assert!(
            source.to_string().contains(SOURCE_MARKER),
            "fixture must actually produce sensitive source diagnostics"
        );
        pool.close().await;
        let app = build_app(cluster).await;
        let logs = LogBuffer::default();
        // Keep callsites enabled when other tests run with no scoped subscriber.
        static GLOBAL: std::sync::Once = std::sync::Once::new();
        GLOBAL.call_once(|| {
            tracing::subscriber::set_global_default(
                tracing_subscriber::fmt()
                    .with_max_level(tracing::Level::TRACE)
                    .with_writer(std::io::sink)
                    .finish(),
            )
            .expect("install integration log subscriber");
        });
        let writer = logs.clone();
        let subscriber = tracing_subscriber::fmt()
            .with_ansi(false)
            .without_time()
            .with_max_level(tracing::Level::TRACE)
            .with_writer(move || writer.clone())
            .finish();
        let response = get(
            &app,
            "/funds/166009/nav?start=2026-09-23&end=2026-09-24",
            Some(&auth_header()),
        )
        .with_subscriber(subscriber)
        .await;
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        let body = body_string(response).await;
        assert_eq!(body, "{\"error\":\"internal_error\"}");
        let logs = String::from_utf8(logs.0.lock().unwrap().clone()).unwrap();
        assert!(
            logs.contains("internal_error"),
            "must capture actual API failure logs: {logs}"
        );
        for marker in [
            API_PASS,
            RO_PASS,
            "postgresql://",
            "166009",
            SOURCE_MARKER,
            "SELECT",
            "start=",
        ] {
            assert!(!body.contains(marker), "500 body leaked {marker:?}");
            assert!(!logs.contains(marker), "logs leaked {marker:?}");
        }
    }
);

#[derive(Clone, Default)]
struct LogBuffer(Arc<Mutex<Vec<u8>>>);
impl std::io::Write for LogBuffer {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[tokio::test]
async fn startup_times_out_when_tcp_accepts_but_never_replies() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!(
        "postgresql://ro:SecretDbMarker@{}/fofoca_it",
        listener.local_addr().unwrap()
    );
    let accepted = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let server_accepted = accepted.clone();
    let server = tokio::spawn(async move {
        let (socket, _) = listener.accept().await.unwrap();
        server_accepted.store(true, std::sync::atomic::Ordering::SeqCst);
        tokio::time::sleep(Duration::from_secs(20)).await;
        drop(socket);
    });
    let config =
        Config::load_for_integration_test(&url, API_USER, API_PASS, "127.0.0.1:8080", 3660);
    let start = Instant::now();
    let result = tokio::time::timeout(
        Duration::from_secs(6),
        fofoca_api::router_for_integration_test(config),
    )
    .await;
    server.abort();
    assert!(
        accepted.load(std::sync::atomic::Ordering::SeqCst),
        "TCP connection must actually have been accepted"
    );
    let error = result
        .expect("startup exceeded five-second connect bound")
        .expect_err("nonresponsive server must fail");
    assert!(start.elapsed() >= Duration::from_secs(4));
    assert_eq!(error.category(), "internal_error");
    assert!(!error.to_string().contains("SecretDbMarker"));
    assert!(std::error::Error::source(&error).is_some());
}

#[test]
fn cleanup_on_success_initialization_failure_and_panic() {
    if !require_gate("cleanup") {
        return;
    }
    let cluster = Cluster::start();
    let dir = cluster.dir.clone();
    let error = cluster.cleanup("false").unwrap_err();
    assert!(error.contains("retained"));
    assert!(cluster.data_dir().join("postmaster.pid").exists());
    assert!(cluster.log_file().exists());
    drop(cluster);
    assert!(!dir.exists(), "success cleanup leaked cluster");

    for initialized in [false, true] {
        let cluster = if initialized {
            Cluster::start()
        } else {
            Cluster::allocate()
        };
        let dir = cluster.dir.clone();
        let failure = std::panic::catch_unwind(move || {
            let _guard = cluster;
            if initialized {
                panic!("injected test failure");
            }
            run_ok(
                pg_command("initdb").arg("--invalid-integration-test-option"),
                "injected initdb failure",
            );
        });
        assert!(failure.is_err());
        assert!(!dir.exists(), "failure cleanup leaked {}", dir.display());
    }
}
