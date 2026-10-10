//! `fofoca-api`: authenticated, read-only HTTP access to stored FOFoca fund
//! NAV data.
//!
//! This crate is an independent companion to the Python `fofoca-data`
//! application. It only reads the existing `fund` and `fund_nav_daily`
//! tables; it never initializes schema, migrates, writes, or fetches
//! upstream data.
//!
//! The library facade exposes [`run`] for the binary entry point, the
//! external [`Config`] contract, and the crate-wide [`Result`] / [`Error`]
//! types. Implementation modules remain private.

mod auth;
mod config;
mod db;
mod error;
mod http;
mod query;

pub use config::{Config, DEFAULT_BIND, DEFAULT_MAX_RANGE_DAYS};
pub use error::{Error, Result};
#[doc(hidden)]
pub use http::HttpService;

/// Build the production router with a connected bounded pool.
///
/// Exposed for the crate's isolated integration tests, which run the real
/// router/middleware stack in-process against a disposable cluster. Not
/// part of the supported public API surface.
#[doc(hidden)]
pub async fn router_for_integration_test(config: Config) -> Result<HttpService> {
    let pool = db::connect(&config).await?;
    Ok(router_with_pool_for_integration_test(config, pool))
}

/// Build the complete authenticated service with a supplied test pool.
/// Allows isolated tests to hold the same pool's connections and verify
/// rejection paths and acquisition timeouts without a second pool.
#[doc(hidden)]
pub fn router_with_pool_for_integration_test(config: Config, pool: sqlx::PgPool) -> HttpService {
    http::router(http::AppState::with_pool(config, pool))
}

/// Build only the bounded pool (used by timeout integration tests).
#[doc(hidden)]
pub async fn connect_pool_for_integration_test(database_url: &str) -> sqlx::PgPool {
    let config = Config::load_for_integration_test(
        database_url,
        "pooltest",
        "pooltest",
        "127.0.0.1:8080",
        3660,
    );
    db::connect(&config)
        .await
        .expect("integration pool must connect")
}

/// Run the HTTP service until shutdown.
///
/// Loads external configuration, connects the bounded read-only pool
/// (failing safely before listening when PostgreSQL is unavailable),
/// builds the fully authenticated router, and serves it on the configured
/// bind address. No schema initialization or migration ever runs here.
pub async fn run() -> Result<()> {
    let config = Config::from_env()?;
    let pool = db::connect(&config).await?;
    let bind = config.bind();
    let state = http::AppState::with_pool(config, pool);
    let app = http::router(state);

    let listener = tokio::net::TcpListener::bind(bind)
        .await
        .map_err(|source| Error::internal("bind listener", source))?;
    tracing::info!(bind = %bind, "fofoca-api listening");
    axum::serve(listener, tower::make::Shared::new(app))
        .await
        .map_err(|source| Error::internal("serve HTTP", source))?;
    Ok(())
}
