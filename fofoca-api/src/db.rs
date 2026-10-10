//! Read-only, bounded PostgreSQL access for stored NAV queries.
//!
//! One parameterized runtime SELECT joins `fund` to `fund_nav_daily` with
//! `$1` code, `$2` start, `$3` end, ordered by `nav_date ASC`. The pool
//! allows at most five connections with a five-second connect/acquire
//! bound and applies a ten-second PostgreSQL statement timeout per
//! connection. No schema initialization, migration, mutation, or upstream
//! access ever happens here. SQLx statement logging is disabled directly on
//! the connection options, independently of the subscriber filter.

use chrono::NaiveDate;
use rust_decimal::Decimal;
use sqlx::ConnectOptions;
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};

use crate::config::Config;
use crate::error::Error;

/// Maximum pool connections (bounded resource usage).
const MAX_CONNECTIONS: u32 = 5;

/// Connect/acquire timeout bounds pool waits.
const ACQUIRE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(5);

/// PostgreSQL per-statement timeout applied to every connection.
const STATEMENT_TIMEOUT_MS: u64 = 10_000;

/// Runtime SELECT matching the Python CLI's stored-data semantics.
const NAV_SELECT: &str = "\
SELECT n.nav_date, n.unit_nav, n.accumulated_nav
FROM fund_nav_daily n
JOIN fund f ON f.id = n.fund_id
WHERE f.code = $1
  AND n.nav_date >= $2
  AND n.nav_date <= $3
ORDER BY n.nav_date ASC
";

/// One stored NAV row with lossless decimal decoding.
pub(crate) struct StoredNavRow {
    pub(crate) nav_date: NaiveDate,
    pub(crate) unit_nav: Option<Decimal>,
    pub(crate) accumulated_nav: Option<Decimal>,
}

/// Build the bounded read-only pool and verify connectivity.
///
/// Called during startup; failure aborts before listening. The pool only
/// validates that PostgreSQL is reachable — it performs no schema setup.
pub(crate) async fn connect(config: &Config) -> Result<sqlx::PgPool, Error> {
    let options = config
        .database_url()
        .parse::<PgConnectOptions>()
        .map_err(|source| {
            Error::config_with_source("FOFOCA_DATABASE_URL must be a valid PostgreSQL URL", source)
        })?
        // Defense in depth: declare the session read-only. Real protection
        // comes from the database role's SELECT-only grants.
        .options([("default_transaction_read_only", "on")])
        .disable_statement_logging();

    let pool = PgPoolOptions::new()
        .max_connections(MAX_CONNECTIONS)
        .acquire_timeout(ACQUIRE_TIMEOUT)
        .after_connect(|conn, _meta| {
            Box::pin(async move {
                sqlx::query(&format!("SET statement_timeout = {STATEMENT_TIMEOUT_MS}"))
                    .execute(conn)
                    .await?;
                Ok(())
            })
        })
        .connect_with(options)
        .await
        .map_err(|source| Error::internal("connect database", source))?;
    Ok(pool)
}

/// Fetch stored rows for one code within inclusive bounds.
///
/// An unknown code, a fund without NAV, or an empty interval yields an
/// empty vector — never an error. Database failures propagate as
/// sanitized internal errors; they are never disguised as empty results.
pub(crate) async fn query_nav_range(
    pool: &sqlx::PgPool,
    code: &str,
    start: NaiveDate,
    end: NaiveDate,
) -> Result<Vec<StoredNavRow>, Error> {
    let rows: Vec<(NaiveDate, Option<Decimal>, Option<Decimal>)> = sqlx::query_as(NAV_SELECT)
        .bind(code)
        .bind(start)
        .bind(end)
        .fetch_all(pool)
        .await
        .map_err(|source| Error::internal("query stored NAV", source))?;
    Ok(rows
        .into_iter()
        .map(|(nav_date, unit_nav, accumulated_nav)| StoredNavRow {
            nav_date,
            unit_nav,
            accumulated_nav,
        })
        .collect())
}
