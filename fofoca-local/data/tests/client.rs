use fofoca_local_data::{Config, Error, fetch_nav_history};
use serde_json::{Value, json};
use std::{
    io::{Read, Write},
    net::TcpListener,
    sync::mpsc,
    thread,
    time::Duration,
};

fn config(url: &str) -> Config {
    Config::new(
        url,
        "user",
        "password",
        Duration::from_millis(100),
        Duration::from_millis(200),
    )
    .unwrap()
}

fn server(
    status: u16,
    body: String,
    headers: String,
    delay: Duration,
) -> (String, mpsc::Receiver<String>, thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let (tx, rx) = mpsc::channel();
    let handle = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let mut request = Vec::new();
        while !request.ends_with(b"\r\n\r\n") {
            let mut byte = [0];
            if stream.read(&mut byte).unwrap_or(0) == 0 {
                break;
            }
            request.push(byte[0]);
        }
        tx.send(String::from_utf8(request).unwrap()).unwrap();
        thread::sleep(delay);
        let _ = write!(
            stream,
            "HTTP/1.1 {status} Mock\r\nContent-Length: {}\r\nConnection: close\r\n{headers}\r\n{body}",
            body.len()
        );
    });
    (url, rx, handle)
}

fn body(rows: Value) -> Value {
    json!({"code":"000001", "start":"2026-01-01", "end":"2026-01-05", "rows":rows})
}

fn row(day: &str, unit: Value, accumulated: Value) -> Value {
    json!({"nav_date":day, "unit_nav":unit, "accumulated_nav":accumulated})
}

fn query(value: Value) -> Result<fofoca_local_data::History, Error> {
    let (url, rx, handle) = server(200, value.to_string(), String::new(), Duration::ZERO);
    let result = fetch_nav_history("000001", "2026-01-01", "2026-01-05", &config(&url));
    let request = rx.recv().unwrap();
    assert!(
        request.starts_with("GET /funds/000001/nav?start=2026-01-01&end=2026-01-05 HTTP/1.1\r\n")
    );
    assert!(
        request
            .to_lowercase()
            .contains("authorization: basic dxnlcjpwyxnzd29yza==")
    );
    handle.join().unwrap();
    result
}

#[test]
fn exact_nullable_distinct_and_gaps() {
    let rows = json!([
        row("2026-01-01", json!("1.230000"), json!("9.000001")),
        row("2026-01-03", Value::Null, json!("2.000000")),
        row("2026-01-04", json!("0.100000"), Value::Null),
        row("2026-01-05", Value::Null, Value::Null)
    ]);
    let history = query(body(rows)).unwrap();
    assert_eq!(history.rows.len(), 4);
    assert_eq!(history.rows[0].unit_nav.as_deref(), Some("1.230000"));
    assert_eq!(history.rows[0].accumulated_nav.as_deref(), Some("9.000001"));
    assert_eq!(history.rows[1].nav_date, "2026-01-03");
    assert_eq!(history.rows[1].unit_nav, None);
    assert_eq!(history.rows[2].accumulated_nav, None);
    assert_eq!(history.rows[3].unit_nav, None);
    assert!(query(body(json!([]))).unwrap().rows.is_empty());
}

#[test]
fn invalid_configuration_and_inputs_are_local_and_redacted() {
    for url in [
        "",
        "bad",
        "file:///tmp/a",
        "https://user:password@example.test/",
        "https://example.test/?password",
        "https://example.test/#password",
        "https://example.test/prefix",
    ] {
        let error = Config::new(
            url,
            "user",
            "password",
            Duration::from_secs(1),
            Duration::from_secs(2),
        )
        .unwrap_err();
        assert_eq!(error, Error::Configuration);
        redacted(&error);
    }
    for (username, password) in [
        ("", "password"),
        ("user", ""),
        ("user:name", "password"),
        ("user\n", "password"),
        ("user", "pass\rword"),
    ] {
        assert!(
            Config::new(
                "https://example.test",
                username,
                password,
                Duration::from_secs(1),
                Duration::from_secs(2)
            )
            .is_err()
        );
    }
    for (connect, overall) in [(0, 1), (1, 0), (2, 1), (1, 301)] {
        assert!(
            Config::new(
                "https://example.test",
                "user",
                "password",
                Duration::from_secs(connect),
                Duration::from_secs(overall)
            )
            .is_err()
        );
    }
    let cfg = config("http://127.0.0.1:1");
    assert_eq!(format!("{cfg:?}"), "Config([REDACTED])");
    for (code, start, end) in [
        ("12345", "2026-01-01", "2026-01-05"),
        ("１２３４５６", "2026-01-01", "2026-01-05"),
        ("../abc", "2026-01-01", "2026-01-05"),
        ("000001", "2026-1-1", "2026-01-05"),
        ("000001", "2026-02-30", "2026-03-01"),
        ("000001", "0000-01-01", "2026-01-05"),
        ("000001", "2026-01-05", "2026-01-01"),
    ] {
        assert_eq!(fetch_nav_history(code, start, end, &cfg), Err(Error::Input));
    }
}

#[test]
fn inconsistent_contracts_never_return_partial_rows() {
    let good = row("2026-01-01", json!("1.000000"), Value::Null);
    let base = body(json!([good.clone()]));
    let mut cases = Vec::new();
    for (field, value) in [
        ("code", json!("000002")),
        ("start", json!("2025-12-31")),
        ("end", json!("2026-01-06")),
        ("rows", Value::Null),
        ("code", json!(1)),
    ] {
        let mut altered = base.clone();
        altered[field] = value;
        cases.push(altered);
    }
    for day in [
        "2025-12-31",
        "2026-01-06",
        "2026-01-01",
        "2026-02-30",
        "2026-1-2",
    ] {
        cases.push(body(json!([
            good.clone(),
            row(day, json!("1"), Value::Null)
        ])));
    }
    cases.push(body(json!([
        row("2026-01-03", Value::Null, Value::Null),
        good.clone()
    ])));
    for metric in ["unit_nav", "accumulated_nav"] {
        for value in [
            json!(1.5),
            json!(true),
            json!([]),
            json!({}),
            json!("NaN"),
            json!("1e3"),
            json!(""),
            json!("1."),
            json!(" 1"),
            json!("１"),
        ] {
            let mut bad = good.clone();
            bad[metric] = value;
            cases.push(body(json!([good.clone(), bad])));
        }
        let mut bad = good.clone();
        bad.as_object_mut().unwrap().remove(metric);
        cases.push(body(json!([bad])));
    }
    for value in cases {
        assert_eq!(query(value), Err(Error::Response));
    }
}

fn redacted(error: &Error) {
    for text in [error.to_string(), format!("{error:?}")] {
        for secret in ["user", "password", "Authorization", "dXNlcjpwYXNzd29yZA=="] {
            assert!(!text.contains(secret), "{text}");
        }
    }
    assert!(std::error::Error::source(error).is_none());
}

#[test]
fn status_malformed_transport_and_timeout_are_typed_and_redacted() {
    for status in [201, 204, 301, 302, 307, 308, 400, 401, 403, 404, 429, 500] {
        let (url, rx, handle) = server(
            status,
            "user password Authorization dXNlcjpwYXNzd29yZA==".into(),
            String::new(),
            Duration::ZERO,
        );
        let error =
            fetch_nav_history("000001", "2026-01-01", "2026-01-05", &config(&url)).unwrap_err();
        assert_eq!(error, Error::Http { status });
        redacted(&error);
        rx.recv().unwrap();
        handle.join().unwrap();
    }
    for malformed in ["not JSON user password", "{", "null", "{}", "[]"] {
        let (url, rx, handle) = server(200, malformed.into(), String::new(), Duration::ZERO);
        let error =
            fetch_nav_history("000001", "2026-01-01", "2026-01-05", &config(&url)).unwrap_err();
        assert_eq!(error, Error::Response);
        redacted(&error);
        rx.recv().unwrap();
        handle.join().unwrap();
    }
    let (url, rx, handle) = server(
        200,
        body(json!([])).to_string(),
        String::new(),
        Duration::from_millis(400),
    );
    let error = fetch_nav_history("000001", "2026-01-01", "2026-01-05", &config(&url)).unwrap_err();
    assert_eq!(error, Error::Transport { timeout: true });
    redacted(&error);
    rx.recv().unwrap();
    handle.join().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    drop(listener);
    let error = fetch_nav_history("000001", "2026-01-01", "2026-01-05", &config(&url)).unwrap_err();
    assert!(matches!(error, Error::Transport { .. }));
    redacted(&error);
}

#[test]
fn redirects_never_contact_another_origin() {
    let target = TcpListener::bind("127.0.0.1:0").unwrap();
    target.set_nonblocking(true).unwrap();
    for status in [301, 302, 303, 307, 308] {
        let location = format!(
            "Location: http://user:password@{}/secret?password\r\n",
            target.local_addr().unwrap()
        );
        let (url, rx, handle) = server(status, String::new(), location, Duration::ZERO);
        let error =
            fetch_nav_history("000001", "2026-01-01", "2026-01-05", &config(&url)).unwrap_err();
        assert_eq!(error, Error::Http { status });
        redacted(&error);
        assert!(
            rx.recv()
                .unwrap()
                .to_lowercase()
                .contains("authorization: basic ")
        );
        handle.join().unwrap();
        assert_eq!(
            target.accept().unwrap_err().kind(),
            std::io::ErrorKind::WouldBlock
        );
    }
}
