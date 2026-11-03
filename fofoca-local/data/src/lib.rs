//! Explicit, read-only, synchronous access to stored NAVs. No ambient configuration.
use chrono::{Datelike, NaiveDate};
use reqwest::{Url, blocking::Client, redirect::Policy};
use serde::{Deserialize, Deserializer};
use snafu::Snafu;
use std::{fmt, time::Duration};

#[derive(Debug, PartialEq, Eq, Snafu)]
pub enum Error {
    #[snafu(display("Invalid client configuration"))]
    Configuration,
    #[snafu(display("Invalid fund code or inclusive date range"))]
    Input,
    #[snafu(display("API transport failed (timeout: {timeout})"))]
    Transport { timeout: bool },
    #[snafu(display("API returned HTTP status {status}"))]
    Http { status: u16 },
    #[snafu(display("Invalid API response contract"))]
    Response,
}

#[derive(Clone)]
pub struct Config {
    base_url: Url,
    username: String,
    password: String,
    connect_timeout: Duration,
    timeout: Duration,
}

impl fmt::Debug for Config {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Config([REDACTED])")
    }
}

impl Config {
    pub fn new(
        base_url: &str,
        username: &str,
        password: &str,
        connect_timeout: Duration,
        timeout: Duration,
    ) -> Result<Self, Error> {
        let base_url = Url::parse(base_url).map_err(|_| Error::Configuration)?;
        if !matches!(base_url.scheme(), "http" | "https")
            || base_url.host_str().is_none()
            || !base_url.username().is_empty()
            || base_url.password().is_some()
            || base_url.query().is_some()
            || base_url.fragment().is_some()
            || base_url.path() != "/"
            || username.is_empty()
            || username.contains(':')
            || password.is_empty()
            || username
                .chars()
                .chain(password.chars())
                .any(char::is_control)
            || connect_timeout.is_zero()
            || timeout.is_zero()
            || connect_timeout > timeout
            || timeout > Duration::from_secs(300)
        {
            return Err(Error::Configuration);
        }
        Ok(Self {
            base_url,
            username: username.into(),
            password: password.into(),
            connect_timeout,
            timeout,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct History {
    pub code: String,
    pub start: String,
    pub end: String,
    pub rows: Vec<NavRow>,
}

// deserialize_with makes a missing metric an error, while accepting explicit null.
fn nullable<'de, D: Deserializer<'de>>(d: D) -> Result<Option<String>, D::Error> {
    Option::<String>::deserialize(d)
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct NavRow {
    pub nav_date: String,
    #[serde(deserialize_with = "nullable")]
    pub unit_nav: Option<String>,
    #[serde(deserialize_with = "nullable")]
    pub accumulated_nav: Option<String>,
}

fn date(value: &str) -> Option<NaiveDate> {
    let parsed = NaiveDate::parse_from_str(value, "%Y-%m-%d").ok()?;
    ((1..=9999).contains(&parsed.year()) && parsed.format("%Y-%m-%d").to_string() == value)
        .then_some(parsed)
}

fn decimal(value: &str) -> bool {
    let value = value.strip_prefix('-').unwrap_or(value);
    let mut parts = value.split('.');
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    digits(parts.next().unwrap_or("")) && parts.next().is_none_or(digits) && parts.next().is_none()
}

pub fn fetch_nav_history(
    code: &str,
    start: &str,
    end: &str,
    config: &Config,
) -> Result<History, Error> {
    if code.len() != 6 || !code.bytes().all(|b| b.is_ascii_digit()) {
        return Err(Error::Input);
    }
    let (Some(first), Some(last)) = (date(start), date(end)) else {
        return Err(Error::Input);
    };
    if first > last {
        return Err(Error::Input);
    }
    let transport = |e: reqwest::Error| Error::Transport {
        timeout: e.is_timeout(),
    };
    let client = Client::builder()
        .connect_timeout(config.connect_timeout)
        .timeout(config.timeout)
        .redirect(Policy::none())
        .retry(reqwest::retry::never())
        .no_proxy()
        .build()
        .map_err(transport)?;
    let url = config
        .base_url
        .join(&format!("funds/{code}/nav"))
        .map_err(|_| Error::Configuration)?;
    let response = client
        .get(url)
        .query(&[("start", start), ("end", end)])
        .basic_auth(&config.username, Some(&config.password))
        .send()
        .map_err(transport)?;
    // Reject redirects, including same-origin ones; never follow with credentials.
    if response.status().as_u16() != 200 {
        return Err(Error::Http {
            status: response.status().as_u16(),
        });
    }
    let body = response.bytes().map_err(transport)?;
    let history: History = serde_json::from_slice(&body).map_err(|_| Error::Response)?;
    if history.code != code || history.start != start || history.end != end {
        return Err(Error::Response);
    }
    let mut previous = None;
    for row in &history.rows {
        let day = date(&row.nav_date).ok_or(Error::Response)?;
        if day < first
            || day > last
            || previous.is_some_and(|p| day <= p)
            || row
                .unit_nav
                .iter()
                .chain(row.accumulated_nav.iter())
                .any(|s| !decimal(s))
        {
            return Err(Error::Response);
        }
        previous = Some(day);
    }
    Ok(history)
}
