//! HTTP Basic credential parsing and constant-time comparison.
//!
//! The parser is deliberately small and standards-compatible: exactly one
//! `Authorization` header, case-insensitive `Basic` scheme, canonical
//! base64 (standard alphabet, padding required), valid UTF-8, and the
//! first colon separating username from password (passwords may contain
//! further colons). Credential comparison uses the reviewed `subtle`
//! crate's constant-time primitives rather than hand-rolled cryptography.

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use http::HeaderMap;
use http::header::AUTHORIZATION;
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;

/// Outcome of decoding a Basic `Authorization` header value.
pub(crate) enum BasicParse {
    /// Well-formed `username:password` credentials.
    Credentials(String, String),
    /// Any malformed/unsupported input; all map to the same generic 401.
    Reject,
}

/// Extract and decode credentials from request headers.
///
/// Rejects missing, duplicated, non-Basic, non-base64, non-UTF-8, and
/// separator-less values. Never echoes supplied material into results.
pub(crate) fn parse_basic(headers: &HeaderMap) -> BasicParse {
    let mut values = headers.get_all(AUTHORIZATION).iter();
    let Some(first) = values.next() else {
        return BasicParse::Reject;
    };
    // A duplicate Authorization header is ambiguous; reject.
    if values.next().is_some() {
        return BasicParse::Reject;
    }
    let Ok(value) = first.to_str() else {
        return BasicParse::Reject;
    };
    let (scheme, token) = split_scheme(value);
    if !scheme.eq_ignore_ascii_case("basic") {
        return BasicParse::Reject;
    }
    let Some(token) = token.filter(|t| !t.is_empty()) else {
        return BasicParse::Reject;
    };
    let Ok(decoded) = BASE64_STANDARD.decode(token) else {
        return BasicParse::Reject;
    };
    let Ok(text) = String::from_utf8(decoded) else {
        return BasicParse::Reject;
    };
    let Some((username, password)) = text.split_once(':') else {
        return BasicParse::Reject;
    };
    if username.is_empty() || password.is_empty() {
        return BasicParse::Reject;
    }
    BasicParse::Credentials(username.to_string(), password.to_string())
}

/// Split `scheme token` at the first run of whitespace.
fn split_scheme(value: &str) -> (&str, Option<&str>) {
    let trimmed = value.trim_start();
    match trimmed.find(char::is_whitespace) {
        Some(index) => {
            let (scheme, rest) = trimmed.split_at(index);
            (scheme, Some(rest.trim()))
        }
        None => (trimmed, None),
    }
}

/// Compare supplied credentials against the configured account in
/// fixed-length SHA-256 digests with reviewed constant-time primitives.
/// Hashing work depends on input length, but comparison never branches on
/// credential length or the first mismatching byte. This is an in-memory
/// comparison, not password storage or a password KDF.
pub(crate) fn credentials_match(
    expected_user: &str,
    expected_pass: &str,
    supplied_user: &str,
    supplied_pass: &str,
) -> bool {
    let user_ok = Sha256::digest(expected_user).ct_eq(&Sha256::digest(supplied_user));
    let pass_ok = Sha256::digest(expected_pass).ct_eq(&Sha256::digest(supplied_pass));
    (user_ok & pass_ok).into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use http::HeaderValue;

    fn headers_with(value: &str) -> HeaderMap {
        let mut map = HeaderMap::new();
        map.insert(AUTHORIZATION, HeaderValue::from_str(value).unwrap());
        map
    }

    fn encode(raw: &str) -> String {
        BASE64_STANDARD.encode(raw)
    }

    #[test]
    fn valid_credentials_parse() {
        let headers = headers_with(&format!("Basic {}", encode("user:pass")));
        match parse_basic(&headers) {
            BasicParse::Credentials(u, p) => {
                assert_eq!(u, "user");
                assert_eq!(p, "pass");
            }
            BasicParse::Reject => panic!("valid credentials rejected"),
        }
    }

    #[test]
    fn scheme_is_case_insensitive() {
        for scheme in ["basic", "BASIC", "BaSiC"] {
            let headers = headers_with(&format!("{scheme} {}", encode("u:p")));
            assert!(matches!(parse_basic(&headers), BasicParse::Credentials(..)));
        }
    }

    #[test]
    fn password_may_contain_colons() {
        let headers = headers_with(&format!("Basic {}", encode("user:p:a:s:s")));
        match parse_basic(&headers) {
            BasicParse::Credentials(u, p) => {
                assert_eq!(u, "user");
                assert_eq!(p, "p:a:s:s");
            }
            BasicParse::Reject => panic!("colon password rejected"),
        }
    }

    #[test]
    fn missing_header_is_rejected() {
        assert!(matches!(parse_basic(&HeaderMap::new()), BasicParse::Reject));
    }

    #[test]
    fn duplicate_headers_are_rejected() {
        let mut map = HeaderMap::new();
        map.append(
            AUTHORIZATION,
            HeaderValue::from_str(&format!("Basic {}", encode("u:p"))).unwrap(),
        );
        map.append(
            AUTHORIZATION,
            HeaderValue::from_str(&format!("Basic {}", encode("u:p"))).unwrap(),
        );
        assert!(matches!(parse_basic(&map), BasicParse::Reject));
    }

    #[test]
    fn invalid_base64_is_rejected() {
        let headers = headers_with("Basic !!!not-base64!!!");
        assert!(matches!(parse_basic(&headers), BasicParse::Reject));
    }

    #[test]
    fn invalid_utf8_is_rejected() {
        let headers = headers_with(&format!("Basic {}", BASE64_STANDARD.encode([0xff, 0xfe])));
        assert!(matches!(parse_basic(&headers), BasicParse::Reject));
    }

    #[test]
    fn missing_separator_is_rejected() {
        let headers = headers_with(&format!("Basic {}", encode("nocolon")));
        assert!(matches!(parse_basic(&headers), BasicParse::Reject));
    }

    #[test]
    fn empty_username_or_password_is_rejected() {
        for raw in [":pass", "user:", ":"] {
            let headers = headers_with(&format!("Basic {}", encode(raw)));
            assert!(matches!(parse_basic(&headers), BasicParse::Reject));
        }
    }

    #[test]
    fn unsupported_scheme_is_rejected() {
        let headers = headers_with("Bearer abcdef");
        assert!(matches!(parse_basic(&headers), BasicParse::Reject));
    }

    #[test]
    fn missing_token_is_rejected() {
        let headers = headers_with("Basic");
        assert!(matches!(parse_basic(&headers), BasicParse::Reject));
    }

    #[test]
    fn constant_time_comparison_matches_and_rejects() {
        assert!(credentials_match("user", "pass", "user", "pass"));
        assert!(!credentials_match("user", "pass", "user", "wrong"));
        assert!(!credentials_match("user", "pass", "wrong", "pass"));
        assert!(!credentials_match("user", "pass", "user", "pass-longer"));
        assert!(!credentials_match("user", "pass", "u", "pass"));
    }
}
