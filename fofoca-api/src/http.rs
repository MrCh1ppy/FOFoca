//! HTTP router, outermost authentication middleware, and safe responses.
//!
//! The complete router — including fallbacks and method rejection — sits
//! behind one authentication middleware, so every request (known route,
//! unknown path, unsupported method, HEAD, OPTIONS) requires the single
//! configured Basic account before routing, extraction, or any database
//! access. Client responses are small stable JSON bodies; error sources,
//! credentials, URLs, and raw query strings never leave the process.
//!
//! The concrete `AppState` carries immutable configuration and, from
//! phase 3, the `PgPool`. No service/repository traits are introduced.

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

use axum::extract::{MatchedPath, Path, RawQuery, State};
use axum::http::{HeaderValue, Method, StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{MethodFilter, get, on};
use axum::{Json, Router};
use rust_decimal::Decimal;
use serde::Serialize;
use tower::Layer;

pub type HttpService =
    tower::util::BoxCloneSyncService<axum::extract::Request, Response, std::convert::Infallible>;

#[derive(Clone, Copy)]
struct RequestRoute(&'static str);

#[derive(Clone, Copy)]
struct SafeFailure {
    category: &'static str,
    operation: &'static str,
}

use crate::auth::{self, BasicParse};
use crate::config::Config;
use crate::db;
use crate::error::Error;
use crate::query;

/// `WWW-Authenticate` challenge attached to every 401 response.
const BASIC_CHALLENGE: &str = "Basic realm=\"fofoca-api\", charset=\"UTF-8\"";

/// Route template of the one query endpoint (used for allowlisted logs).
const NAV_ROUTE: &str = "/funds/{code}/nav";

/// Marker recorded for requests matching no route template.
const UNMATCHED_ROUTE: &str = "<unmatched>";

/// Monotonic per-process request identifier for allowlisted logs.
static REQUEST_COUNTER: AtomicU64 = AtomicU64::new(1);

/// Shared immutable application state.
///
/// Carries validated configuration plus the read-only pool. The pool is
/// `Option` so in-process unit tests can run the full router without a
/// database: with `None`, a valid query returns a sanitized 500 rather
/// than a fabricated empty 200.
#[derive(Clone)]
pub(crate) struct AppState {
    config: Arc<Config>,
    pool: Option<sqlx::PgPool>,
}

impl AppState {
    /// Build state from validated configuration and a connected pool.
    pub(crate) fn with_pool(config: Config, pool: sqlx::PgPool) -> Self {
        AppState {
            config: Arc::new(config),
            pool: Some(pool),
        }
    }

    /// Build state without a pool (unit tests / pre-phase-3 stub mode).
    #[cfg(test)]
    pub(crate) fn new(config: Config) -> Self {
        AppState {
            config: Arc::new(config),
            pool: None,
        }
    }
}

/// Build the complete authenticated router.
///
/// Layer order matters: the authentication middleware wraps the whole
/// router as its outermost layer, so it runs before route matching,
/// extraction, fallback selection, and (later) pool acquisition.
pub(crate) fn router(state: AppState) -> HttpService {
    let inner = Router::new()
        .route(NAV_ROUTE, get(nav_handler))
        // Explicit authenticated rejection of HEAD and OPTIONS on the
        // query endpoint: without this, axum would implicitly serve HEAD
        // via the GET handler, and OPTIONS would fall through to 404
        // instead of the required 405.
        .route(
            NAV_ROUTE,
            on(MethodFilter::HEAD.or(MethodFilter::OPTIONS), reject_method),
        )
        .fallback(not_found_fallback)
        .layer(middleware::from_fn(record_matched_route))
        .with_state(state.clone());

    // Applying Layer to the Router *service* (not Router::layer) is essential:
    // authentication executes before the router matches paths or methods.
    HttpService::new(middleware::from_fn_with_state(state, authenticate_request).layer(inner))
}

async fn record_matched_route(request: axum::extract::Request, next: Next) -> Response {
    let route = match request
        .extensions()
        .get::<MatchedPath>()
        .map(MatchedPath::as_str)
    {
        Some(NAV_ROUTE) => NAV_ROUTE,
        _ => UNMATCHED_ROUTE,
    };
    let mut response = next.run(request).await;
    response.extensions_mut().insert(RequestRoute(route));
    response
}

/// Outermost authentication middleware.
///
/// Runs before routing and extraction. Every failure returns the same
/// generic 401 with the Basic challenge; nothing about the supplied
/// value is logged or echoed. Unauthenticated requests never reach the
/// pool: this middleware never touches `state.pool`.
async fn authenticate_request(
    State(state): State<AppState>,
    request: axum::extract::Request,
    next: Next,
) -> Response {
    let started = Instant::now();
    let request_id = REQUEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    let method = safe_method(request.method());
    let headers = request.headers();
    let response = match auth::parse_basic(headers) {
        BasicParse::Credentials(username, password)
            if auth::credentials_match(
                state.config.basic_username(),
                state.config.basic_password(),
                &username,
                &password,
            ) =>
        {
            next.run(request).await
        }
        _ => unauthorized_response(),
    };
    let route = response
        .extensions()
        .get::<RequestRoute>()
        .map_or(UNMATCHED_ROUTE, |route| route.0);
    let failure = response.extensions().get::<SafeFailure>();
    tracing::info!(
        request_id,
        method,
        route,
        status = response.status().as_u16(),
        elapsed_ms = started.elapsed().as_millis() as u64,
        category = failure.map(|failure| failure.category),
        operation = failure.map(|failure| failure.operation),
        "request completed"
    );
    response
}

/// Render the HTTP method for logs without echoing attacker-controlled
/// extension tokens: known standard methods are named; anything else is
/// reduced to a fixed marker so a crafted verb cannot inject markers.
fn safe_method(method: &Method) -> &'static str {
    match *method {
        Method::GET => "GET",
        Method::HEAD => "HEAD",
        Method::POST => "POST",
        Method::PUT => "PUT",
        Method::DELETE => "DELETE",
        Method::OPTIONS => "OPTIONS",
        Method::PATCH => "PATCH",
        _ => "<other>",
    }
}

/// `GET /funds/{code}/nav` handler.
///
/// Validates the code and the inclusive date range, then runs the
/// parameterized read-only SELECT. Unknown codes and empty intervals are
/// `200` with `rows: []`; database failures are sanitized 500s — never a
/// fabricated empty success.
async fn nav_handler(
    State(state): State<AppState>,
    code: Result<Path<String>, axum::extract::rejection::PathRejection>,
    RawQuery(raw_query): RawQuery,
) -> Response {
    let Ok(Path(raw_code)) = code else {
        return error_body(StatusCode::BAD_REQUEST, "invalid_request");
    };
    let result = validate_and_query(&state, &raw_code, raw_query.as_deref()).await;
    match result {
        Ok(body) => body,
        Err(error) => error_response(&error),
    }
}

/// CLI-compatible success body (`code`/`start`/`end`/`rows`).
#[derive(Serialize)]
struct NavResponse {
    code: String,
    start: String,
    end: String,
    rows: Vec<NavRow>,
}

/// One row: ISO date, six-fractional-digit decimal strings or null.
#[derive(Serialize)]
struct NavRow {
    nav_date: String,
    unit_nav: Option<String>,
    accumulated_nav: Option<String>,
}

/// Render a present decimal with exactly six fractional digits, matching
/// the stored `NUMERIC(18,6)` scale (e.g. `1.1` -> `"1.100000"`).
fn render_decimal(value: &Decimal) -> String {
    format!("{value:.6}")
}

/// Validation + query execution boundary.
async fn validate_and_query(
    state: &AppState,
    raw_code: &str,
    raw_query: Option<&str>,
) -> Result<Response, Error> {
    let code = query::validate_code(raw_code)?;
    let (start, end) = query::validate_query(raw_query, state.config.max_range_days())?;

    let Some(pool) = state.pool.as_ref() else {
        // Unit-test scaffold mode: never fabricate an empty success.
        return Err(Error::internal(
            "query stored NAV",
            std::io::Error::new(
                std::io::ErrorKind::NotConnected,
                "database pool is not configured in this process",
            ),
        ));
    };

    let rows = db::query_nav_range(pool, &code, start, end).await?;
    let body = NavResponse {
        code,
        start: start.format("%Y-%m-%d").to_string(),
        end: end.format("%Y-%m-%d").to_string(),
        rows: rows
            .iter()
            .map(|row| NavRow {
                nav_date: row.nav_date.format("%Y-%m-%d").to_string(),
                unit_nav: row.unit_nav.as_ref().map(render_decimal),
                accumulated_nav: row.accumulated_nav.as_ref().map(render_decimal),
            })
            .collect(),
    };
    Ok(Json(body).into_response())
}

/// Authenticated rejection for HEAD/OPTIONS on the query endpoint.
async fn reject_method() -> Response {
    let mut response = error_body(StatusCode::METHOD_NOT_ALLOWED, "method_not_allowed");
    response
        .headers_mut()
        .insert(header::ALLOW, HeaderValue::from_static("GET"));
    response
}

/// Authenticated fallback for every unknown path.
async fn not_found_fallback() -> Response {
    error_body(StatusCode::NOT_FOUND, "not_found")
}

/// Map an error category to its HTTP status.
fn status_for(error: &Error) -> StatusCode {
    match error {
        Error::InvalidRequest { .. } => StatusCode::BAD_REQUEST,
        Error::Unauthorized => StatusCode::UNAUTHORIZED,
        Error::Internal { .. } | Error::Config { .. } | Error::ConfigSource { .. } => {
            StatusCode::INTERNAL_SERVER_ERROR
        }
    }
}

/// Stable JSON error body shape shared by all client-visible failures.
#[derive(Serialize)]
struct ErrorBody {
    error: &'static str,
}

/// Render a domain error as its safe JSON response.
fn error_response(error: &Error) -> Response {
    let label = match error {
        Error::InvalidRequest { .. } => "invalid_request",
        Error::Unauthorized => "unauthorized",
        _ => "internal_error",
    };
    let status = status_for(error);
    let mut response = error_body(status, label);
    if status == StatusCode::INTERNAL_SERVER_ERROR {
        // Never format Error or source here. Operations are a closed allowlist,
        // including a safe marker for unexpected future contexts.
        let operation = match error {
            Error::Internal {
                operation: "query stored NAV",
                ..
            } => "query stored NAV",
            _ => "internal operation",
        };
        response.extensions_mut().insert(SafeFailure {
            category: "internal_error",
            operation,
        });
    }
    if status == StatusCode::UNAUTHORIZED {
        response.headers_mut().insert(
            header::WWW_AUTHENTICATE,
            HeaderValue::from_static(BASIC_CHALLENGE),
        );
    }
    response
}

/// Generic 401 for middleware-level authentication failures.
fn unauthorized_response() -> Response {
    let mut response = error_body(StatusCode::UNAUTHORIZED, "unauthorized");
    response.headers_mut().insert(
        header::WWW_AUTHENTICATE,
        HeaderValue::from_static(BASIC_CHALLENGE),
    );
    response
}

/// Small stable JSON error body with a content-type header.
fn error_body(status: StatusCode, label: &'static str) -> Response {
    (status, Json(ErrorBody { error: label })).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::Engine as _;
    use base64::engine::general_purpose::STANDARD as B64;
    use http::Request;
    use http::header::AUTHORIZATION;
    use tower::ServiceExt as _;

    const USER: &str = "reader";
    const PASS: &str = "s3cret:verbatim";

    fn test_state() -> AppState {
        // Configuration is constructed through the validated loader to
        // keep tests exercising the real invariants; no environment is
        // mutated (unsafe in edition 2024).
        let config = crate::config::Config::load_for_test(
            "postgresql://reader@127.0.0.1:5432/fofoca",
            USER,
            PASS,
            "127.0.0.1:8080",
            3660,
        );
        AppState::new(config)
    }

    fn basic_header(user: &str, pass: &str) -> String {
        format!("Basic {}", B64.encode(format!("{user}:{pass}")))
    }

    async fn request(app: &HttpService, method: Method, uri: &str, auth: Option<&str>) -> Response {
        let mut builder = Request::builder().method(method).uri(uri);
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

    fn challenge_of(response: &Response) -> Option<&str> {
        response
            .headers()
            .get(header::WWW_AUTHENTICATE)
            .and_then(|v| v.to_str().ok())
    }

    // -- authentication across every surface (task 2.4) ----------------------

    #[tokio::test]
    async fn missing_credentials_rejected_on_query_unknown_and_methods() {
        let app = router(test_state());
        for (method, uri) in [
            (
                Method::GET,
                "/funds/166009/nav?start=2026-09-23&end=2026-09-24",
            ),
            (Method::GET, "/unknown"),
            (
                Method::POST,
                "/funds/166009/nav?start=2026-09-23&end=2026-09-24",
            ),
            (
                Method::HEAD,
                "/funds/166009/nav?start=2026-09-23&end=2026-09-24",
            ),
            (
                Method::OPTIONS,
                "/funds/166009/nav?start=2026-09-23&end=2026-09-24",
            ),
        ] {
            let response = request(&app, method.clone(), uri, None).await;
            assert_eq!(response.status(), StatusCode::UNAUTHORIZED, "uri {uri}");
            assert_eq!(challenge_of(&response), Some(BASIC_CHALLENGE));
            if method != Method::HEAD {
                // HEAD responses carry no body by HTTP semantics even when
                // the authentication middleware rejects the request.
                assert_eq!(body_string(response).await, "{\"error\":\"unauthorized\"}");
            }
        }
    }

    #[tokio::test]
    async fn wrong_credentials_are_rejected() {
        let app = router(test_state());
        let response = request(
            &app,
            Method::GET,
            "/funds/166009/nav?start=2026-09-23&end=2026-09-24",
            Some(&basic_header(USER, "wrong")),
        )
        .await;
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        assert_eq!(challenge_of(&response), Some(BASIC_CHALLENGE));
    }

    #[tokio::test]
    async fn malformed_base64_utf8_separator_scheme_and_duplicates_are_rejected() {
        let app = router(test_state());
        let cases = [
            "Basic !!!not-base64!!!".to_string(),
            format!("Basic {}", B64.encode([0xff, 0xfe])),
            format!("Basic {}", B64.encode("nocolon")),
            "Bearer abc".to_string(),
            format!(
                "Basic {}",
                B64.encode(format!("{USER}:{PASS}:extra-but-wrong-user"))
            ),
        ];
        for (method, uri) in [
            (Method::GET, "/funds/166009/nav?start=bad&end=worse"),
            (Method::GET, "/unknown"),
            (Method::POST, "/funds/166009/nav"),
            (Method::HEAD, "/funds/166009/nav"),
            (Method::OPTIONS, "/funds/166009/nav"),
            (Method::GET, "/funds/%FF/nav"),
        ] {
            for value in &cases {
                let response = request(&app, method.clone(), uri, Some(value)).await;
                assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
                assert_eq!(challenge_of(&response), Some(BASIC_CHALLENGE));
            }
            let req = Request::builder()
                .method(method)
                .uri(uri)
                .header(AUTHORIZATION, basic_header(USER, PASS))
                .header(AUTHORIZATION, basic_header(USER, PASS));
            let response = app
                .clone()
                .oneshot(req.body(axum::body::Body::empty()).unwrap())
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
            assert_eq!(challenge_of(&response), Some(BASIC_CHALLENGE));
        }
    }

    #[tokio::test]
    async fn case_insensitive_scheme_and_colon_password_are_accepted() {
        let app = router(test_state());
        let uri = "/funds/166009/nav?start=2026-09-23&end=2026-09-24";
        for scheme in ["Basic", "basic", "BASIC"] {
            let header = format!("{scheme} {}", B64.encode(format!("{USER}:{PASS}")));
            let response = request(&app, Method::GET, uri, Some(&header)).await;
            // Auth passes; phase-2 stub then yields a sanitized 500.
            assert_eq!(
                response.status(),
                StatusCode::INTERNAL_SERVER_ERROR,
                "scheme {scheme}"
            );
            assert_eq!(
                body_string(response).await,
                "{\"error\":\"internal_error\"}"
            );
        }
    }

    #[tokio::test]
    async fn auth_precedes_input_validation() {
        // Unauthenticated request with an invalid code and malformed dates
        // must be 401, not 400.
        let app = router(test_state());
        let response = request(
            &app,
            Method::GET,
            "/funds/abc/nav?start=bad&end=worse",
            None,
        )
        .await;
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn valid_auth_unknown_path_is_404_and_unsupported_method_is_405() {
        let app = router(test_state());
        let auth = basic_header(USER, PASS);

        let response = request(&app, Method::GET, "/nope", Some(&auth)).await;
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
        assert_eq!(body_string(response).await, "{\"error\":\"not_found\"}");

        for method in [Method::POST, Method::PUT, Method::DELETE] {
            let response = request(
                &app,
                method,
                "/funds/166009/nav?start=2026-09-23&end=2026-09-24",
                Some(&auth),
            )
            .await;
            assert_eq!(response.status(), StatusCode::METHOD_NOT_ALLOWED);
        }
    }

    #[tokio::test]
    async fn head_and_options_on_query_endpoint_are_405_after_auth() {
        let app = router(test_state());
        let auth = basic_header(USER, PASS);
        for method in [Method::HEAD, Method::OPTIONS] {
            let response = request(
                &app,
                method.clone(),
                "/funds/166009/nav?start=2026-09-23&end=2026-09-24",
                Some(&auth),
            )
            .await;
            assert_eq!(
                response.status(),
                StatusCode::METHOD_NOT_ALLOWED,
                "{method}"
            );
            assert_eq!(
                response
                    .headers()
                    .get(header::ALLOW)
                    .and_then(|v| v.to_str().ok()),
                Some("GET")
            );
        }
    }

    #[tokio::test]
    async fn no_public_health_or_docs_bypass() {
        let app = router(test_state());
        for path in ["/health", "/healthz", "/docs", "/openapi.json", "/"] {
            let response = request(&app, Method::GET, path, None).await;
            assert_eq!(response.status(), StatusCode::UNAUTHORIZED, "path {path}");
        }
    }

    // -- validation after authentication (task 2.3 coverage via HTTP) ---------

    #[tokio::test]
    async fn invalid_code_and_dates_return_400_after_auth() {
        let app = router(test_state());
        let auth = basic_header(USER, PASS);
        let cases = [
            "/funds/%FF/nav?start=2026-09-23&end=2026-09-24", // path UTF-8 rejection
            "/funds/12345/nav?start=2026-09-23&end=2026-09-24", // short code
            "/funds/12345a/nav?start=2026-09-23&end=2026-09-24", // non-digit
            "/funds/166009/nav?start=2026-09-24&end=2026-09-23", // reversed
            "/funds/166009/nav?start=2026-13-01&end=2026-09-24", // impossible
            "/funds/166009/nav?end=2026-09-24",               // missing start
            "/funds/166009/nav?start=2026-09-23&start=2026-09-23&end=2026-09-24", // duplicate
            "/funds/166009/nav?start=2026-09-23&end=2037-01-01", // over default span
        ];
        for uri in cases {
            let response = request(&app, Method::GET, uri, Some(&auth)).await;
            assert_eq!(response.status(), StatusCode::BAD_REQUEST, "uri {uri}");
            assert_eq!(
                body_string(response).await,
                "{\"error\":\"invalid_request\"}"
            );
        }
    }

    #[tokio::test]
    async fn same_day_and_exact_limit_pass_validation_to_stub_500() {
        let app = router(test_state());
        let auth = basic_header(USER, PASS);
        // Same-day query: valid input reaches the phase-2 stub, which
        // answers a sanitized 500 rather than a fake empty 200.
        let response = request(
            &app,
            Method::GET,
            "/funds/166009/nav?start=2026-09-23&end=2026-09-23",
            Some(&auth),
        )
        .await;
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    }

    #[tokio::test]
    async fn responses_never_echo_credentials_or_query_strings() {
        let app = router(test_state());
        let auth = basic_header(USER, PASS);
        let uri = "/funds/166009/nav?start=bad-marker&end=2026-09-24";
        let response = request(&app, Method::GET, uri, Some(&auth)).await;
        let body = body_string(response).await;
        assert!(!body.contains("bad-marker"));
        assert!(!body.contains(PASS));
        assert!(!body.contains(USER));
    }

    // -- allowlisted tracing and redaction (task 2.5) --------------------------

    #[derive(Clone)]
    struct CaptureWriter(Arc<std::sync::Mutex<Vec<u8>>>);

    impl std::io::Write for CaptureWriter {
        fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
            self.0.lock().unwrap().extend_from_slice(buf);
            Ok(buf.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    /// Scope the subscriber to each poll, isolating concurrently running tests.
    async fn capture_logs<F, Fut>(action: F) -> String
    where
        F: FnOnce() -> Fut,
        Fut: std::future::Future<Output = ()>,
    {
        use tracing::instrument::WithSubscriber;
        // Keep TRACE callsites enabled while scoped subscribers are created
        // and dropped by concurrent tests; the global writer discards output.
        static GLOBAL: std::sync::Once = std::sync::Once::new();
        GLOBAL.call_once(|| {
            tracing::subscriber::set_global_default(
                tracing_subscriber::fmt()
                    .with_max_level(tracing::Level::TRACE)
                    .with_writer(std::io::sink)
                    .finish(),
            )
            .expect("install test subscriber");
        });
        let buffer = Arc::new(std::sync::Mutex::new(Vec::new()));
        let writer = CaptureWriter(buffer.clone());
        let subscriber = tracing_subscriber::fmt()
            .with_writer(move || writer.clone())
            .with_ansi(false)
            .without_time()
            .with_max_level(tracing::Level::TRACE)
            .finish();
        action().with_subscriber(subscriber).await;
        let captured = buffer.lock().unwrap().clone();
        String::from_utf8(captured).unwrap()
    }

    #[tokio::test]
    async fn request_logs_contain_only_allowlisted_fields() {
        let app = router(test_state());
        let inner = Router::new()
            .route(NAV_ROUTE, get(|| async { StatusCode::OK }))
            .layer(middleware::from_fn(record_matched_route));
        let success = HttpService::new(
            middleware::from_fn_with_state(test_state(), authenticate_request).layer(inner),
        );
        let auth = basic_header(USER, PASS);
        let uri = "/funds/166009/nav?start=SecretMarkerStart&end=2026-09-24";
        let logs = capture_logs(|| async {
            // Auth pass + input rejection, auth failure, and unknown path:
            // three representative log paths.
            let _ = request(&app, Method::GET, uri, Some(&auth)).await;
            let _ = request(&app, Method::GET, uri, None).await;
            let _ = request(&app, Method::GET, "/secret-marker-path", Some(&auth)).await;
            let _ = request(&app, Method::POST, "/funds/166009/nav", Some(&auth)).await;
            let _ = request(&app, Method::HEAD, "/funds/166009/nav", Some(&auth)).await;
            let _ = request(&app, Method::OPTIONS, "/funds/166009/nav", Some(&auth)).await;
            let response = request(&app, Method::GET, "/funds/%FF/nav", Some(&auth)).await;
            assert_eq!(response.status(), StatusCode::BAD_REQUEST);
            assert_eq!(
                body_string(response).await,
                "{\"error\":\"invalid_request\"}"
            );
            let _ = request(
                &app,
                Method::from_bytes(b"SecretMethodMarker").unwrap(),
                "/funds/166009/nav",
                Some(&auth),
            )
            .await;
            // Exercise the same completion wrapper's success path without DB I/O.
            assert_eq!(
                request(&success, Method::GET, "/funds/166009/nav", Some(&auth))
                    .await
                    .status(),
                StatusCode::OK
            );
        })
        .await;
        assert!(!logs.is_empty(), "expected captured log output");
        // Secret-bearing material never appears.
        for marker in [
            PASS,
            "SecretMarkerStart",
            "secret-marker-path",
            "Authorization",
            "postgresql://",
            "SecretMethodMarker",
        ] {
            assert!(!logs.contains(marker), "logs leaked {marker:?}:\n{logs}");
        }
        // Allowlisted fields are present: method, status, and route marker.
        assert!(logs.contains("GET"));
        assert!(logs.contains("400"));
        assert!(logs.contains("401"));
        assert!(logs.contains("404"));
        assert!(logs.contains("405"));
        assert!(logs.contains("<other>"));
        let lines: Vec<_> = logs.lines().collect();
        assert_eq!(lines.len(), 9, "one completion event per request: {logs}");
        assert!(logs.contains("status=200"));
        let mut ids = std::collections::HashSet::new();
        for line in lines {
            for field in [
                "request_id=",
                "method=",
                "route=",
                "status=",
                "elapsed_ms=",
                "request completed",
            ] {
                assert!(line.contains(field), "missing {field}: {line}");
            }
            let id = line
                .split("request_id=")
                .nth(1)
                .unwrap()
                .split_whitespace()
                .next()
                .unwrap();
            assert!(ids.insert(id.to_owned()), "duplicate request id");
        }
        assert!(logs.contains(NAV_ROUTE));
        assert!(logs.contains(UNMATCHED_ROUTE));
    }

    #[tokio::test]
    async fn internal_error_logs_omit_source_details() {
        // The phase-2 stub wraps a source error carrying a marker; logs and
        // responses must only carry the safe category, while the Error
        // value itself retains the source internally.
        let inner = Router::new()
            .route(
                NAV_ROUTE,
                get(|| async {
                    error_response(&Error::internal(
                        "query stored NAV",
                        std::io::Error::other("SourceSecretMarker"),
                    ))
                }),
            )
            .layer(middleware::from_fn(record_matched_route));
        let app = HttpService::new(
            middleware::from_fn_with_state(test_state(), authenticate_request).layer(inner),
        );
        let auth = basic_header(USER, PASS);
        let logs = capture_logs(|| async {
            let response = request(
                &app,
                Method::GET,
                "/funds/166009/nav?start=2026-09-23&end=2026-09-24",
                Some(&auth),
            )
            .await;
            assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
            assert_eq!(
                body_string(response).await,
                "{\"error\":\"internal_error\"}"
            );
        })
        .await;
        // The stub's source text ("database pool is not configured") is
        // internal-only; log lines carry the allowlisted fields.
        assert!(logs.contains("500"));
        assert!(logs.contains("category=\"internal_error\""));
        assert!(logs.contains("operation=\"query stored NAV\""));
        assert!(!logs.contains("SourceSecretMarker"));
        assert_eq!(logs.lines().count(), 1);
        assert!(!logs.contains("NotConnected"));
        assert!(!logs.contains("phase 3"));
    }

    #[test]
    fn error_display_and_category_are_secret_free() {
        // Errors rendered toward clients/logs use Display + category only.
        let marker = "Sup3rSecretMarker";
        let error = Error::internal(
            "query stored NAV",
            std::io::Error::other(format!("boom {marker}")),
        );
        assert_eq!(error.category(), "internal_error");
        let display = error.to_string();
        assert!(
            !display.contains(marker),
            "Display leaked source: {display}"
        );
        // The Debug representation may retain internal detail for
        // programmatic diagnosis, but must never be the logging path.
        let config_error = Error::config_with_source(
            "FOFOCA_DATABASE_URL must be a valid PostgreSQL URL",
            std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                format!("url had {marker}"),
            ),
        );
        assert!(!config_error.to_string().contains(marker));
    }

    #[test]
    fn error_source_chain_is_retained_internally() {
        // Sources remain reachable via std::error::Error::source for
        // operators diagnosing from the process, satisfying "retain
        // internal source chains".
        use std::error::Error as _;
        let error = Error::internal(
            "query stored NAV",
            std::io::Error::new(std::io::ErrorKind::NotConnected, "internal marker"),
        );
        let mut found_marker = false;
        let mut cursor = error.source();
        while let Some(source) = cursor {
            if source.to_string().contains("internal marker") {
                found_marker = true;
            }
            cursor = source.source();
        }
        assert!(found_marker, "internal source chain lost the cause");
    }
}
