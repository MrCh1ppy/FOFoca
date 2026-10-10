//! Strict query/path validation for the NAV endpoint.
//!
//! All checks run after authentication and before any database access.
//! Codes are exactly six ASCII digits; `start`/`end` must each appear
//! exactly once as strict `YYYY-MM-DD` real dates; the inclusive span is
//! computed with checked calendar arithmetic and bounded by the configured
//! maximum.

use chrono::NaiveDate;

use crate::error::Error;

/// Validate the path fund code: exactly six ASCII digits.
pub(crate) fn validate_code(code: &str) -> Result<String, Error> {
    if code.len() == 6 && code.bytes().all(|b| b.is_ascii_digit()) {
        Ok(code.to_string())
    } else {
        Err(Error::invalid_request(
            "fund code must be exactly six ASCII digits",
        ))
    }
}

/// Parse one strict `YYYY-MM-DD` date: exact shape, real calendar day.
fn parse_strict_date(raw: &str) -> Result<NaiveDate, Error> {
    let bytes = raw.as_bytes();
    let shape_ok = bytes.len() == 10
        && bytes[4] == b'-'
        && bytes[7] == b'-'
        && bytes
            .iter()
            .enumerate()
            .all(|(i, b)| i == 4 || i == 7 || b.is_ascii_digit());
    if !shape_ok {
        return Err(Error::invalid_request(
            "dates must use strict YYYY-MM-DD format",
        ));
    }
    NaiveDate::parse_from_str(raw, "%Y-%m-%d")
        .map_err(|_| Error::invalid_request("dates must be real calendar dates"))
}

/// Extract exactly one `start` and one `end` from the raw query string.
///
/// The raw string is parsed with `serde_urlencoded` into a sequence of
/// pairs so duplicates are detectable; a typed struct deserialization
/// would silently collapse repeated keys.
pub(crate) fn validate_query(
    raw_query: Option<&str>,
    max_range_days: i64,
) -> Result<(NaiveDate, NaiveDate), Error> {
    let raw = raw_query.unwrap_or("");
    let pairs: Vec<(String, String)> = serde_urlencoded::from_str(raw)
        .map_err(|_| Error::invalid_request("query string is not valid form encoding"))?;

    let mut starts = pairs.iter().filter(|(k, _)| k == "start");
    let mut ends = pairs.iter().filter(|(k, _)| k == "end");

    let start_raw = match (starts.next(), starts.next()) {
        (Some((_, v)), None) => v,
        (None, _) => return Err(Error::invalid_request("missing start parameter")),
        (Some(_), Some(_)) => {
            return Err(Error::invalid_request("duplicate start parameter"));
        }
    };
    let end_raw = match (ends.next(), ends.next()) {
        (Some((_, v)), None) => v,
        (None, _) => return Err(Error::invalid_request("missing end parameter")),
        (Some(_), Some(_)) => {
            return Err(Error::invalid_request("duplicate end parameter"));
        }
    };

    let start = parse_strict_date(start_raw)?;
    let end = parse_strict_date(end_raw)?;

    if start > end {
        return Err(Error::invalid_request("start date is after end date"));
    }

    let days = end
        .signed_duration_since(start)
        .num_days()
        .checked_add(1)
        .ok_or_else(|| Error::invalid_request("date range overflows calendar arithmetic"))?;
    if days > max_range_days {
        return Err(Error::invalid_request(
            "date range exceeds the configured maximum",
        ));
    }

    Ok((start, end))
}

#[cfg(test)]
mod tests {
    use super::*;

    const MAX: i64 = 3660;

    // -- code validation ----------------------------------------------------

    #[test]
    fn valid_six_digit_code_is_accepted() {
        assert_eq!(validate_code("166009").unwrap(), "166009");
        assert_eq!(validate_code("000001").unwrap(), "000001");
    }

    #[test]
    fn code_wrong_length_is_rejected() {
        for code in ["12345", "1234567", ""] {
            assert!(validate_code(code).is_err(), "accepted {code:?}");
        }
    }

    #[test]
    fn code_with_non_digit_is_rejected() {
        for code in ["12345a", "1234 6", "１２３４５６", "12345\u{1}"] {
            assert!(validate_code(code).is_err(), "accepted {code:?}");
        }
    }

    #[test]
    fn fullwidth_digits_are_non_ascii_and_rejected() {
        // Six fullwidth digits are 18 bytes and non-ASCII.
        assert!(validate_code("１６６００９").is_err());
    }

    // -- date parameter presence/multiplicity --------------------------------

    #[test]
    fn missing_start_is_rejected() {
        assert!(validate_query(Some("end=2026-09-24"), MAX).is_err());
    }

    #[test]
    fn missing_end_is_rejected() {
        assert!(validate_query(Some("start=2026-09-23"), MAX).is_err());
    }

    #[test]
    fn empty_query_string_is_rejected() {
        assert!(validate_query(Some(""), MAX).is_err());
        assert!(validate_query(None, MAX).is_err());
    }

    #[test]
    fn duplicate_start_is_rejected() {
        assert!(
            validate_query(
                Some("start=2026-09-23&start=2026-09-23&end=2026-09-24"),
                MAX
            )
            .is_err()
        );
    }

    #[test]
    fn duplicate_end_is_rejected() {
        assert!(
            validate_query(Some("start=2026-09-23&end=2026-09-24&end=2026-09-25"), MAX).is_err()
        );
    }

    // -- date format ----------------------------------------------------------

    #[test]
    fn non_iso_dates_are_rejected() {
        for raw in [
            "09/23/2026",
            "20260923",
            "2026-9-23",
            "2026-09-2",
            "2026-09-23T00:00:00",
        ] {
            let query = format!("start={raw}&end=2026-09-24");
            assert!(validate_query(Some(&query), MAX).is_err(), "accepted {raw}");
        }
    }

    #[test]
    fn impossible_dates_are_rejected() {
        for raw in ["2026-02-30", "2026-13-01", "2026-00-10", "2026-01-00"] {
            let query = format!("start={raw}&end={raw}");
            assert!(validate_query(Some(&query), MAX).is_err(), "accepted {raw}");
        }
    }

    #[test]
    fn leap_day_rules_apply() {
        assert!(validate_query(Some("start=2024-02-29&end=2024-02-29"), MAX).is_ok());
        assert!(validate_query(Some("start=2026-02-29&end=2026-02-29"), MAX).is_err());
    }

    // -- range semantics -------------------------------------------------------

    #[test]
    fn reversed_bounds_are_rejected() {
        assert!(validate_query(Some("start=2026-09-24&end=2026-09-23"), MAX).is_err());
    }

    #[test]
    fn same_day_counts_as_one_and_is_accepted() {
        let (start, end) = validate_query(Some("start=2026-09-23&end=2026-09-23"), MAX).unwrap();
        assert_eq!(start, end);
    }

    #[test]
    fn exact_maximum_boundary_is_accepted() {
        // 2024-01-01 through 2027-12-31 is 1461 days; build exact-limit
        // cases from a small configured maximum instead.
        let (start, end) = validate_query(Some("start=2026-01-01&end=2026-01-10"), 10).unwrap();
        assert_eq!(start.to_string(), "2026-01-01");
        assert_eq!(end.to_string(), "2026-01-10");
    }

    #[test]
    fn one_day_over_maximum_is_rejected() {
        assert!(validate_query(Some("start=2026-01-01&end=2026-01-11"), 10).is_err());
    }

    #[test]
    fn default_limit_accepts_ten_year_span() {
        // 2016-01-01..2025-12-31 inclusive is 3652 days (< 3660).
        assert!(validate_query(Some("start=2016-01-01&end=2025-12-31"), MAX).is_ok());
    }

    #[test]
    fn default_limit_rejects_eleven_year_span() {
        assert!(validate_query(Some("start=2016-01-01&end=2026-12-31"), MAX).is_err());
    }

    #[test]
    fn url_encoded_dates_are_decoded() {
        // Percent-encoded form of start=2026-09-23&end=2026-09-24.
        let (start, end) =
            validate_query(Some("start=2026%2D09%2D23&end=2026%2D09%2D24"), MAX).unwrap();
        assert_eq!(start.to_string(), "2026-09-23");
        assert_eq!(end.to_string(), "2026-09-24");
    }

    #[test]
    fn malformed_query_encoding_is_rejected() {
        assert!(validate_query(Some("start=%ZZ&end=2026-09-24"), MAX).is_err());
    }
}
