//! Crate-wide error type and SNAFU categories.
//!
//! Error categories map to the HTTP contract: invalid request (400),
//! authentication (401), and internal/database failures (500). Startup
//! configuration failures are a separate category that terminates before
//! listening. Source chains are retained internally for diagnostics but
//! are never serialized to clients or logs by the HTTP layer.

use snafu::Snafu;

/// Crate-wide result type.
pub type Result<T, E = Error> = std::result::Result<T, E>;

/// Retained underlying cause for startup configuration failures.
///
/// Keeps the original parse/validation error as an opaque internal source;
/// it is never formatted toward clients or logs.
#[derive(Debug)]
pub struct ConfigSourceError(Box<dyn std::error::Error + Send + Sync + 'static>);

impl std::fmt::Display for ConfigSourceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // Deliberately terse: configuration source details stay internal
        // and are only surfaced via `source()` when explicitly walked.
        f.write_str("configuration source error")
    }
}

impl std::error::Error for ConfigSourceError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(self.0.as_ref())
    }
}

/// Retained underlying cause for internal/database failures.
///
/// Behaves like [`ConfigSourceError`]: the wrapped error is reachable only
/// via `source()` and is never formatted toward clients or logs.
#[derive(Debug)]
pub struct InternalSourceError(Box<dyn std::error::Error + Send + Sync + 'static>);

impl std::fmt::Display for InternalSourceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("internal source error")
    }
}

impl std::error::Error for InternalSourceError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(self.0.as_ref())
    }
}

/// Top-level error categories for the API.
///
/// `Display` output is intentionally generic and never includes supplied
/// configuration values, credentials, URLs, or raw source diagnostics.
/// Use [`snafu::ErrorCompat`] / `std::error::Error::source` internally to
/// walk retained source chains when diagnosing failures.
#[derive(Debug, Snafu)]
#[snafu(visibility(pub(crate)))]
pub enum Error {
    /// Startup configuration is missing, empty, or malformed.
    ///
    /// The message identifies which setting failed but never its value.
    #[snafu(display("invalid configuration: {message}"))]
    Config {
        /// Safe, value-free description of the configuration problem.
        message: String,
    },

    /// Startup configuration failed with a retained underlying cause.
    #[snafu(display("invalid configuration: {message}"))]
    ConfigSource {
        /// Safe, value-free description of the configuration problem.
        message: String,
        /// Underlying parse/validation error, retained internally.
        source: ConfigSourceError,
    },

    /// Request input failed validation (maps to HTTP 400).
    #[snafu(display("invalid request: {message}"))]
    InvalidRequest {
        /// Safe client-facing category detail.
        message: String,
    },

    /// Authentication failed (maps to HTTP 401).
    #[snafu(display("unauthorized"))]
    Unauthorized,

    /// Database or internal failure (maps to HTTP 500).
    #[snafu(display("internal error during {operation}"))]
    Internal {
        /// Safe operation label, e.g. `"connect database"`.
        operation: &'static str,
        /// Underlying error, retained internally only.
        source: InternalSourceError,
    },
}

impl Error {
    /// Build a configuration error with a safe message and no source.
    pub(crate) fn config(message: impl Into<String>) -> Self {
        Error::Config {
            message: message.into(),
        }
    }

    /// Build a configuration error retaining an underlying source.
    pub(crate) fn config_with_source(
        message: impl Into<String>,
        source: impl std::error::Error + Send + Sync + 'static,
    ) -> Self {
        Error::ConfigSource {
            message: message.into(),
            source: ConfigSourceError(Box::new(source)),
        }
    }

    /// Build an internal error retaining an underlying source (used once
    /// database access lands in a later phase).
    #[allow(dead_code)]
    pub(crate) fn internal(
        operation: &'static str,
        source: impl std::error::Error + Send + Sync + 'static,
    ) -> Self {
        Error::Internal {
            operation,
            source: InternalSourceError(Box::new(source)),
        }
    }

    /// Build an invalid-request error (HTTP 400) with a safe message.
    #[allow(dead_code)] // Used by query validation in this phase.
    pub(crate) fn invalid_request(message: impl Into<String>) -> Self {
        Error::InvalidRequest {
            message: message.into(),
        }
    }

    /// Build an authentication failure (HTTP 401).
    #[allow(dead_code)] // Used by the auth middleware in this phase.
    pub(crate) fn unauthorized() -> Self {
        Error::Unauthorized
    }

    /// Stable public category label used for sanitized client/log output.
    pub fn category(&self) -> &'static str {
        match self {
            Error::Config { .. } | Error::ConfigSource { .. } => "configuration",
            Error::InvalidRequest { .. } => "invalid_request",
            Error::Unauthorized => "unauthorized",
            Error::Internal { .. } => "internal_error",
        }
    }
}
