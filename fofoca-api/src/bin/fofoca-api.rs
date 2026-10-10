//! Executable entry point for the `fofoca-api` HTTP service.
//!
//! Initializes allowlisted tracing, loads external configuration, and runs
//! the service. All diagnostics are sanitized: no environment values,
//! credentials, URLs, or raw source chains are logged.

use fofoca_api::Error;

fn main() -> std::process::ExitCode {
    init_tracing();
    let runtime = match tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
    {
        Ok(runtime) => runtime,
        Err(source) => {
            eprintln!("fofoca-api failed to start: unable to build Tokio runtime");
            // Keep the OS error reachable for operators without printing
            // raw diagnostics into shared logs.
            let _ = source;
            return std::process::ExitCode::FAILURE;
        }
    };
    match runtime.block_on(fofoca_api::run()) {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            report_startup_failure(&error);
            std::process::ExitCode::FAILURE
        }
    }
}

/// Install a tracing subscriber with a strict allowlist policy.
///
/// Only this crate's targets are ever enabled: the default level is OFF
/// for every other target (sqlx, hyper, tokio, tower, rustls, …), so
/// dependency DEBUG/TRACE output that could embed connection URLs, SQL
/// statements, or header material can never be emitted. `RUST_LOG` may
/// only tune the verbosity of `fofoca_api` targets; any directive it
/// carries for a non-allowlisted target is overridden back to OFF.
/// The application itself records only allowlisted fields and never
/// credentials, URLs, raw query strings, or source chains.
fn init_tracing() {
    // Base filter: everything OFF, this crate at info unless RUST_LOG
    // overrides the crate directive.
    let crate_directive = std::env::var("RUST_LOG")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .and_then(|value| sanitize_rust_log(&value))
        .unwrap_or_else(|| "fofoca_api=info".to_string());
    let mut filter = tracing_subscriber::EnvFilter::new("off");
    filter = filter.add_directive(crate_directive.parse().expect("valid directive"));
    // Belt-and-braces: explicitly pin known chatty/sensitive dependency
    // targets to OFF even if a future dependency name slips past the
    // default-OFF base filter through an unexpected directive merge.
    for target in [
        "sqlx",
        "hyper",
        "hyper_util",
        "tokio",
        "tower",
        "tower_http",
        "h2",
        "rustls",
        "axum",
        "tracing",
        "mio",
        "serde_json",
    ] {
        filter = filter.add_directive(format!("{target}=off").parse().expect("valid directive"));
    }
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_target(true)
        .compact()
        .init();
}

/// Keep only allowlisted crate directives from a `RUST_LOG` value.
///
/// Accepts `fofoca_api` / `fofoca_api::*` directives at any level and a
/// bare level (interpreted as the crate level); everything else —
/// dependency targets, span/field selectors — is dropped so an operator
/// filter cannot re-enable non-allowlisted targets or record span fields.
fn sanitize_rust_log(raw: &str) -> Option<String> {
    for part in raw.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        let (target, level) = match part.split_once('=') {
            Some((target, level)) => (target.trim(), level.trim()),
            // A bare level applies to the crate only.
            None => ("fofoca_api", part),
        };
        if target == "fofoca_api" || target.starts_with("fofoca_api::") {
            let level = match level.to_ascii_lowercase().as_str() {
                "error" | "warn" | "info" | "debug" | "trace" | "off" => level,
                _ => continue, // Unknown level or span selector: drop.
            };
            return Some(format!("fofoca_api={level}"));
        }
    }
    None
}

/// Emit a sanitized startup failure line and exit-code context.
fn report_startup_failure(error: &Error) {
    // Display output is generic and value-free by construction; the source
    // chain is retained in `error` for programmatic diagnosis but is not
    // printed here.
    eprintln!(
        "fofoca-api startup failed (category: {}): {error}",
        error.category()
    );
}

#[cfg(test)]
mod tests {
    use super::sanitize_rust_log;

    #[test]
    fn bare_level_applies_to_crate_only() {
        assert_eq!(sanitize_rust_log("debug"), Some("fofoca_api=debug".into()));
    }

    #[test]
    fn crate_directive_is_kept() {
        assert_eq!(
            sanitize_rust_log("fofoca_api=trace"),
            Some("fofoca_api=trace".into())
        );
        assert_eq!(
            sanitize_rust_log("fofoca_api::http=warn"),
            Some("fofoca_api=warn".into())
        );
    }

    #[test]
    fn dependency_targets_are_dropped() {
        assert_eq!(sanitize_rust_log("sqlx=trace"), None);
        assert_eq!(
            sanitize_rust_log("info,sqlx=debug,hyper=trace"),
            Some("fofoca_api=info".into())
        );
    }

    #[test]
    fn span_field_selectors_are_dropped() {
        // `fofoca_api[span{field}]=trace` style selectors must not survive.
        assert_eq!(sanitize_rust_log("fofoca_api[req{password}]=trace"), None);
    }

    #[test]
    fn garbage_falls_back_to_default() {
        assert_eq!(sanitize_rust_log("???"), None);
        assert_eq!(sanitize_rust_log(""), None);
    }
}
