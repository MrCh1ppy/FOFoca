//! External environment configuration with fail-closed validation.
//!
//! All settings come from externally injected environment variables; this
//! crate never loads `.env` files automatically.
//!
//! | Variable | Contract |
//! | --- | --- |
//! | `FOFOCA_DATABASE_URL` | Required nonempty SQLx PostgreSQL URL for the dedicated read-only role |
//! | `FOFOCA_API_BASIC_USERNAME` | Required single-account username; no colon/control characters |
//! | `FOFOCA_API_BASIC_PASSWORD` | Required single-account password; no control characters; kept verbatim |
//! | `FOFOCA_API_BIND` | Socket address, default `127.0.0.1:8080` |
//! | `FOFOCA_API_MAX_RANGE_DAYS` | Positive representable integer, default `3660` |
//!
//! Unset optional settings use defaults; supplied empty or invalid
//! settings fail startup. Diagnostics identify the setting but never its
//! value.

use std::net::SocketAddr;
use std::num::ParseIntError;

use crate::error::Error;

/// Default local bind address (`127.0.0.1:8080`).
pub const DEFAULT_BIND: &str = "127.0.0.1:8080";

/// Default maximum inclusive calendar-day span (approximately ten years).
pub const DEFAULT_MAX_RANGE_DAYS: i64 = 3660;

const ENV_DATABASE_URL: &str = "FOFOCA_DATABASE_URL";
const ENV_BASIC_USERNAME: &str = "FOFOCA_API_BASIC_USERNAME";
const ENV_BASIC_PASSWORD: &str = "FOFOCA_API_BASIC_PASSWORD";
const ENV_BIND: &str = "FOFOCA_API_BIND";
const ENV_MAX_RANGE_DAYS: &str = "FOFOCA_API_MAX_RANGE_DAYS";

/// Validated external configuration.
///
/// This type deliberately does not implement `Debug` because it carries
/// account credentials and a database URL that may embed secrets. Access
/// secrets only through the explicit accessors; never format the whole
/// struct or log raw values.
pub struct Config {
    database_url: String,
    basic_username: String,
    basic_password: String,
    bind: SocketAddr,
    max_range_days: i64,
}

/// Read one environment variable, distinguishing unset from set-but-empty.
fn env_value(name: &str) -> Option<String> {
    match std::env::var(name) {
        Ok(value) => Some(value),
        Err(std::env::VarError::NotPresent) => None,
        Err(std::env::VarError::NotUnicode(_)) => {
            Some(String::new()) // Non-Unicode input can never validate.
        }
    }
}

/// Load a required setting; reject unset, empty, and whitespace-only input.
fn required(read: &dyn Fn(&str) -> Option<String>, name: &str) -> Result<String, Error> {
    match read(name) {
        Some(value) if !value.trim().is_empty() => Ok(value),
        _ => Err(Error::config(format!(
            "{name} must be set to a nonempty value"
        ))),
    }
}

impl Config {
    /// Load and validate configuration from process environment variables.
    ///
    /// Fails closed: any missing or invalid required setting, malformed
    /// optional setting, or invalid bind/limit aborts startup before the
    /// listener exists.
    pub fn from_env() -> Result<Self, Error> {
        Self::load(&|name| env_value(name))
    }

    /// Core loader against an arbitrary variable reader.
    ///
    /// Taking a reader keeps validation deterministic in edition-2024
    /// tests, where mutating the process environment is unsafe.
    fn load(read: &dyn Fn(&str) -> Option<String>) -> Result<Self, Error> {
        let database_url = required(read, ENV_DATABASE_URL)?;
        validate_database_url(&database_url)?;

        let basic_username = required(read, ENV_BASIC_USERNAME)?;
        validate_username(&basic_username)?;

        let basic_password = required(read, ENV_BASIC_PASSWORD)?;
        validate_password(&basic_password)?;

        let bind = match read(ENV_BIND) {
            None => DEFAULT_BIND
                .parse()
                .expect("default bind constant must parse"),
            Some(raw) => parse_bind(&raw)?,
        };

        let max_range_days = match read(ENV_MAX_RANGE_DAYS) {
            None => DEFAULT_MAX_RANGE_DAYS,
            Some(raw) => parse_max_range_days(&raw)?,
        };

        Ok(Config {
            database_url,
            basic_username,
            basic_password,
            bind,
            max_range_days,
        })
    }

    /// SQLx-compatible PostgreSQL URL for the dedicated read-only role.
    #[allow(dead_code)] // Consumed by the pool wiring in phase 3.
    pub(crate) fn database_url(&self) -> &str {
        &self.database_url
    }

    /// Configured Basic username (validated; safe to compare).
    #[allow(dead_code)] // Consumed by the auth middleware in phase 2.
    pub(crate) fn basic_username(&self) -> &str {
        &self.basic_username
    }

    /// Configured Basic password, preserved verbatim.
    #[allow(dead_code)] // Consumed by the auth middleware in phase 2.
    pub(crate) fn basic_password(&self) -> &str {
        &self.basic_password
    }

    /// Validated bind socket address.
    pub fn bind(&self) -> SocketAddr {
        self.bind
    }

    /// Validated maximum inclusive calendar-day span.
    pub fn max_range_days(&self) -> i64 {
        self.max_range_days
    }

    /// Bind address rendered for sanitized startup diagnostics.
    pub fn bind_display(&self) -> impl std::fmt::Display {
        self.bind
    }

    /// Construct a configuration directly for in-process tests, bypassing
    /// environment access. Validity is still enforced through the same
    /// validators used by [`Config::load`].
    #[cfg(test)]
    pub(crate) fn load_for_test(
        database_url: &str,
        username: &str,
        password: &str,
        bind: &str,
        max_range_days: i64,
    ) -> Self {
        validate_database_url(database_url).expect("test URL must be valid");
        validate_username(username).expect("test username must be valid");
        validate_password(password).expect("test password must be valid");
        Config {
            database_url: database_url.to_string(),
            basic_username: username.to_string(),
            basic_password: password.to_string(),
            bind: bind.parse().expect("test bind must parse"),
            max_range_days,
        }
    }

    /// Construct a validated configuration for the crate's isolated
    /// integration tests (external test binary; not part of the supported
    /// public API). Uses the same validators as the production loader.
    #[doc(hidden)]
    pub fn load_for_integration_test(
        database_url: &str,
        username: &str,
        password: &str,
        bind: &str,
        max_range_days: i64,
    ) -> Self {
        validate_database_url(database_url).expect("test URL must be valid");
        validate_username(username).expect("test username must be valid");
        validate_password(password).expect("test password must be valid");
        assert!(max_range_days > 0, "test range must be positive");
        Config {
            database_url: database_url.to_string(),
            basic_username: username.to_string(),
            basic_password: password.to_string(),
            bind: bind.parse().expect("test bind must parse"),
            max_range_days,
        }
    }
}

/// Require an SQLx-compatible PostgreSQL URL without echoing its value.
fn validate_database_url(raw: &str) -> Result<(), Error> {
    let url = url::Url::parse(raw).map_err(|source| {
        Error::config_with_source("FOFOCA_DATABASE_URL must be a valid PostgreSQL URL", source)
    })?;
    if !matches!(url.scheme(), "postgres" | "postgresql") {
        return Err(Error::config(
            "FOFOCA_DATABASE_URL must be a valid PostgreSQL URL",
        ));
    }
    // sqlx::postgres::PgConnectOptions parses URL-form DSNs; FromStr keeps
    // the value inside the error chain but never in our public message.
    raw.parse::<sqlx::postgres::PgConnectOptions>()
        .map(|_| ())
        .map_err(|source| {
            Error::config_with_source(
                format!("{ENV_DATABASE_URL} must be a valid PostgreSQL URL"),
                source,
            )
        })
}

/// Username must exclude colons and control characters.
fn validate_username(value: &str) -> Result<(), Error> {
    if value.contains(':') || value.chars().any(char::is_control) {
        return Err(Error::config(format!(
            "{ENV_BASIC_USERNAME} must not contain ':' or control characters"
        )));
    }
    Ok(())
}

/// Password must exclude control characters but may contain colons; the
/// value is preserved verbatim.
fn validate_password(value: &str) -> Result<(), Error> {
    if value.chars().any(char::is_control) {
        return Err(Error::config(format!(
            "{ENV_BASIC_PASSWORD} must not contain control characters"
        )));
    }
    Ok(())
}

/// Parse a supplied bind setting; empty/whitespace input is invalid.
fn parse_bind(raw: &str) -> Result<SocketAddr, Error> {
    if raw.trim().is_empty() {
        return Err(Error::config(format!(
            "{ENV_BIND} must be nonempty when supplied"
        )));
    }
    raw.parse::<SocketAddr>()
        .map_err(|source: std::net::AddrParseError| {
            Error::config_with_source(format!("{ENV_BIND} must be a valid socket address"), source)
        })
}

/// Parse a supplied limit; must be a positive representable integer.
fn parse_max_range_days(raw: &str) -> Result<i64, Error> {
    if raw.trim().is_empty() {
        return Err(Error::config(format!(
            "{ENV_MAX_RANGE_DAYS} must be nonempty when supplied"
        )));
    }
    let value = raw.parse::<i64>().map_err(|source: ParseIntError| {
        Error::config_with_source(
            format!("{ENV_MAX_RANGE_DAYS} must be a positive representable integer"),
            source,
        )
    })?;
    if value <= 0 {
        return Err(Error::config(format!(
            "{ENV_MAX_RANGE_DAYS} must be a positive integer"
        )));
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    //! Deterministic configuration tests.
    //!
    //! These tests inject variables through [`Config::load`] with a closure
    //! reader instead of mutating the process environment, which is unsafe
    //! under Rust edition 2024. Every case is hermetic and parallel-safe.

    use super::*;

    const VALID_URL: &str = "postgresql://reader@127.0.0.1:5432/fofoca";
    const VALID_USER: &str = "reader";
    const VALID_PASS: &str = "s3cret:verbatim pass";

    /// Reader returning only the supplied required trio with defaults.
    fn base_reader<'a>() -> impl Fn(&str) -> Option<String> + 'a {
        move |name| match name {
            ENV_DATABASE_URL => Some(VALID_URL.to_string()),
            ENV_BASIC_USERNAME => Some(VALID_USER.to_string()),
            ENV_BASIC_PASSWORD => Some(VALID_PASS.to_string()),
            _ => None,
        }
    }

    /// Reader overriding selected names on top of the valid base.
    fn reader_with<'a>(
        overrides: &'a [(&'a str, Option<&'a str>)],
    ) -> impl Fn(&str) -> Option<String> + 'a {
        let base = base_reader();
        move |name| {
            for (key, value) in overrides {
                if *key == name {
                    return value.map(str::to_string);
                }
            }
            base(name)
        }
    }

    fn load_with(overrides: &[(&str, Option<&str>)]) -> Result<Config, Error> {
        Config::load(&reader_with(overrides))
    }

    // -- required settings -------------------------------------------------

    #[test]
    fn missing_database_url_is_rejected() {
        let err = load_with(&[(ENV_DATABASE_URL, None)])
            .err()
            .expect("expected configuration to fail");
        assert_eq!(err.category(), "configuration");
        assert!(err.to_string().contains(ENV_DATABASE_URL));
    }

    #[test]
    fn empty_database_url_is_rejected() {
        let err = load_with(&[(ENV_DATABASE_URL, Some(""))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_DATABASE_URL));
    }

    #[test]
    fn whitespace_only_database_url_is_rejected() {
        let err = load_with(&[(ENV_DATABASE_URL, Some("  \t "))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_DATABASE_URL));
    }

    #[test]
    fn missing_username_is_rejected() {
        let err = load_with(&[(ENV_BASIC_USERNAME, None)])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_BASIC_USERNAME));
    }

    #[test]
    fn whitespace_only_username_is_rejected() {
        let err = load_with(&[(ENV_BASIC_USERNAME, Some("   "))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_BASIC_USERNAME));
    }

    #[test]
    fn missing_password_is_rejected() {
        let err = load_with(&[(ENV_BASIC_PASSWORD, None)])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_BASIC_PASSWORD));
    }

    #[test]
    fn whitespace_only_password_is_rejected() {
        let err = load_with(&[(ENV_BASIC_PASSWORD, Some("  \n "))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_BASIC_PASSWORD));
    }

    // -- value validation ---------------------------------------------------

    #[test]
    fn invalid_database_url_is_rejected() {
        let err = load_with(&[(ENV_DATABASE_URL, Some("not a url"))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_DATABASE_URL));
        // The retained source exists but is not part of Display output.
        let rendered = err.to_string();
        assert!(!rendered.contains("not a url"));
    }

    #[test]
    fn libpq_keyword_dsn_is_rejected() {
        // SQLx needs URL syntax; libpq keyword DSNs must fail closed.
        let err = load_with(&[(ENV_DATABASE_URL, Some("host=localhost dbname=fofoca"))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_DATABASE_URL));
    }

    #[test]
    fn non_postgres_schemes_are_rejected() {
        for raw in [
            "https://reader@localhost/fofoca",
            "mysql://reader@localhost/fofoca",
            "sqlite:///tmp/db",
        ] {
            let error = load_with(&[(ENV_DATABASE_URL, Some(raw))]).err().unwrap();
            assert!(error.to_string().contains(ENV_DATABASE_URL));
            assert!(!error.to_string().contains(raw));
        }
        for raw in ["postgres://reader@localhost/fofoca", VALID_URL] {
            assert!(load_with(&[(ENV_DATABASE_URL, Some(raw))]).is_ok());
        }
    }

    #[test]
    fn username_with_colon_is_rejected() {
        let err = load_with(&[(ENV_BASIC_USERNAME, Some("bad:user"))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_BASIC_USERNAME));
    }

    #[test]
    fn username_with_control_character_is_rejected() {
        let err = load_with(&[(ENV_BASIC_USERNAME, Some("bad\u{7}user"))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_BASIC_USERNAME));
    }

    #[test]
    fn password_with_control_character_is_rejected() {
        let err = load_with(&[(ENV_BASIC_PASSWORD, Some("bad\u{0}pass"))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_BASIC_PASSWORD));
    }

    #[test]
    fn password_with_colon_is_accepted_verbatim() {
        let config = load_with(&[(ENV_BASIC_PASSWORD, Some("p:a:s:s"))]).unwrap();
        assert_eq!(config.basic_password(), "p:a:s:s");
    }

    #[test]
    fn password_is_preserved_verbatim() {
        let config = load_with(&[]).unwrap();
        assert_eq!(config.basic_password(), VALID_PASS);
    }

    // -- optional settings: defaults ----------------------------------------

    #[test]
    fn unset_optional_settings_use_defaults() {
        let config = load_with(&[]).unwrap();
        assert_eq!(config.bind(), DEFAULT_BIND.parse().unwrap());
        assert_eq!(config.max_range_days(), DEFAULT_MAX_RANGE_DAYS);
    }

    #[test]
    fn supplied_valid_overrides_are_used() {
        let config = load_with(&[
            (ENV_BIND, Some("0.0.0.0:9000")),
            (ENV_MAX_RANGE_DAYS, Some("30")),
        ])
        .unwrap();
        assert_eq!(config.bind(), "0.0.0.0:9000".parse().unwrap());
        assert_eq!(config.max_range_days(), 30);
    }

    // -- optional settings: supplied-but-invalid -----------------------------

    #[test]
    fn empty_bind_is_rejected() {
        let err = load_with(&[(ENV_BIND, Some(""))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_BIND));
    }

    #[test]
    fn whitespace_only_bind_is_rejected() {
        let err = load_with(&[(ENV_BIND, Some("   "))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_BIND));
    }

    #[test]
    fn invalid_bind_is_rejected() {
        let err = load_with(&[(ENV_BIND, Some("not-an-address"))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_BIND));
    }

    #[test]
    fn zero_max_range_days_is_rejected() {
        let err = load_with(&[(ENV_MAX_RANGE_DAYS, Some("0"))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_MAX_RANGE_DAYS));
    }

    #[test]
    fn negative_max_range_days_is_rejected() {
        let err = load_with(&[(ENV_MAX_RANGE_DAYS, Some("-5"))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_MAX_RANGE_DAYS));
    }

    #[test]
    fn overflowing_max_range_days_is_rejected() {
        let err = load_with(&[(ENV_MAX_RANGE_DAYS, Some("99999999999999999999"))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_MAX_RANGE_DAYS));
    }

    #[test]
    fn empty_max_range_days_is_rejected() {
        let err = load_with(&[(ENV_MAX_RANGE_DAYS, Some(""))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_MAX_RANGE_DAYS));
    }

    #[test]
    fn whitespace_only_max_range_days_is_rejected() {
        let err = load_with(&[(ENV_MAX_RANGE_DAYS, Some("  "))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_MAX_RANGE_DAYS));
    }

    #[test]
    fn nonnumeric_max_range_days_is_rejected() {
        let err = load_with(&[(ENV_MAX_RANGE_DAYS, Some("ten"))])
            .err()
            .expect("expected configuration to fail");
        assert!(err.to_string().contains(ENV_MAX_RANGE_DAYS));
    }

    // -- secret-free diagnostics ---------------------------------------------

    #[test]
    fn diagnostics_never_echo_supplied_values() {
        // A secret marker embedded in every supplied value must never
        // appear in the rendered error.
        let marker = "Sup3rSecretMarker";
        let err = load_with(&[
            (
                ENV_DATABASE_URL,
                Some("postgresql://u:Sup3rSecretMarker@h/db"),
            ),
            (ENV_BASIC_USERNAME, Some("reader")),
            (ENV_BASIC_PASSWORD, Some("pass")),
            (ENV_BIND, Some(marker)),
            (ENV_MAX_RANGE_DAYS, Some("abc")),
        ])
        .err()
        .expect("expected configuration to fail");
        let rendered = format!("{err}");
        assert!(
            !rendered.contains(marker),
            "diagnostics leaked a supplied value: {rendered}"
        );
    }

    #[test]
    fn config_type_has_no_debug_derive() {
        // Invariant documented in the type rustdoc: `Config` deliberately
        // does not implement `Debug`, so secret-bearing fields cannot be
        // accidentally logged via `{:?}`. This is enforced structurally by
        // the absence of a derive on the struct; this test exists as a
        // readable marker of that decision.
        let config = load_with(&[]).unwrap();
        assert_eq!(config.max_range_days(), DEFAULT_MAX_RANGE_DAYS);
    }
}
