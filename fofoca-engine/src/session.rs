use chrono::{Datelike, NaiveDate};
use snafu::Snafu;

use crate::{
    decimal::{DecimalError, Money},
    model::Account,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    start_date: NaiveDate,
    end_date: NaiveDate,
    initial_cash: Money,
}

#[derive(Debug, Snafu, PartialEq, Eq)]
pub enum ConfigError {
    #[snafu(display("Dates must be in years 0001 through 9999"))]
    DateRange,
    #[snafu(display("start_date must not be after end_date"))]
    ReversedDates,
    #[snafu(display("Invalid initial_cash: {source}"))]
    InitialCash { source: DecimalError },
}

impl Config {
    pub fn new(
        start_date: NaiveDate,
        end_date: NaiveDate,
        initial_cash: Money,
    ) -> Result<Self, ConfigError> {
        if !(1..=9999).contains(&start_date.year()) || !(1..=9999).contains(&end_date.year()) {
            return Err(ConfigError::DateRange);
        }
        if start_date > end_date {
            return Err(ConfigError::ReversedDates);
        }
        Ok(Self {
            start_date,
            end_date,
            initial_cash,
        })
    }

    pub fn parse(
        start_date: NaiveDate,
        end_date: NaiveDate,
        initial_cash: &str,
    ) -> Result<Self, ConfigError> {
        let cash =
            Money::parse(initial_cash).map_err(|source| ConfigError::InitialCash { source })?;
        Self::new(start_date, end_date, cash)
    }

    pub fn start_date(&self) -> NaiveDate {
        self.start_date
    }
    pub fn end_date(&self) -> NaiveDate {
        self.end_date
    }
    pub fn initial_cash(&self) -> Money {
        self.initial_cash
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Running,
}

/// Detached owned data, never a reference into a session's account.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Snapshot {
    pub status: Status,
    pub start_date: NaiveDate,
    pub end_date: NaiveDate,
    pub current_date: NaiveDate,
    pub last_closed_date: Option<NaiveDate>,
    pub snapshot_pinned: bool,
    // Orders do not exist until the separately authorized trading phase.
    pub account: Account,
}

#[derive(Default)]
pub struct Session {
    state: Option<Snapshot>,
}

#[derive(Debug, Snafu, PartialEq, Eq)]
pub enum SessionError {
    #[snafu(display("This business operation is not implemented"))]
    NotImplemented,
    #[snafu(display("Session is not initialized"))]
    NotInitialized,
    #[snafu(display("Session is already initialized"))]
    AlreadyInitialized,
}

impl Session {
    pub fn initialize(&mut self, config: Config) -> Result<(), SessionError> {
        if self.state.is_some() {
            return Err(SessionError::AlreadyInitialized);
        }
        self.state = Some(Snapshot {
            status: Status::Running,
            start_date: config.start_date,
            end_date: config.end_date,
            current_date: config.start_date,
            last_closed_date: None,
            snapshot_pinned: false,
            account: Account::empty(config.start_date, config.initial_cash),
        });
        Ok(())
    }

    pub fn ensure_initialized(&self) -> Result<(), SessionError> {
        self.state
            .as_ref()
            .map(|_| ())
            .ok_or(SessionError::NotInitialized)
    }

    pub fn account(&self) -> Result<Snapshot, SessionError> {
        self.state.clone().ok_or(SessionError::NotInitialized)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn date(year: i32, month: u32, day: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(year, month, day).unwrap()
    }

    #[test]
    fn invalid_then_valid_initialization() {
        let mut session = Session::default();
        let start = date(2026, 1, 1);
        assert_eq!(
            Config::parse(date(2026, 1, 2), start, "100"),
            Err(ConfigError::ReversedDates)
        );
        assert!(matches!(
            Config::parse(start, start, "-1"),
            Err(ConfigError::InitialCash { .. })
        ));
        assert_eq!(session.account(), Err(SessionError::NotInitialized));
        session
            .initialize(Config::parse(start, start, "0").unwrap())
            .unwrap();
        let snapshot = session.account().unwrap();
        assert_eq!(snapshot.status, Status::Running);
        assert_eq!(snapshot.current_date, start);
        assert_eq!(snapshot.last_closed_date, None);
        assert!(!snapshot.snapshot_pinned);
        assert_eq!(snapshot.account, Account::empty(start, Money::ZERO));
    }

    #[test]
    fn inclusive_ranges_and_year_bounds() {
        for day in [date(1, 1, 1), date(9999, 12, 31)] {
            let mut session = Session::default();
            session
                .initialize(Config::parse(day, day, "1").unwrap())
                .unwrap();
            assert_eq!(session.account().unwrap().current_date, day);
        }
        for (start, end) in [
            (date(0, 1, 1), date(1, 1, 1)),
            (date(9999, 1, 1), date(10000, 1, 1)),
        ] {
            assert_eq!(Config::parse(start, end, "0"), Err(ConfigError::DateRange));
        }
        assert!(Config::parse(date(1, 1, 1), date(9999, 12, 31), "0").is_ok());
    }

    #[test]
    fn repeat_initialization_does_not_replace_account() {
        let mut session = Session::default();
        let day = date(2026, 1, 1);
        session
            .initialize(Config::parse(day, day, "100.123456789012").unwrap())
            .unwrap();
        let before = session.account().unwrap();
        assert_eq!(
            session.initialize(Config::parse(date(2027, 1, 1), date(2027, 1, 2), "200").unwrap()),
            Err(SessionError::AlreadyInitialized)
        );
        assert_eq!(session.account().unwrap(), before);
    }

    #[test]
    fn sessions_and_owned_snapshots_are_independent() {
        let mut first = Session::default();
        let mut second = Session::default();
        let day = date(2026, 1, 1);
        first
            .initialize(Config::parse(day, day, "100").unwrap())
            .unwrap();
        assert_eq!(second.account(), Err(SessionError::NotInitialized));
        second
            .initialize(Config::parse(day, day, "200").unwrap())
            .unwrap();
        let mut copy = first.account().unwrap();
        copy.account.cash = Money::ZERO;
        copy.account.pending_cash.push(crate::model::PendingCash {
            code: crate::model::Code::parse("000001").unwrap(),
            amount: Money::ZERO,
            execution_date: day,
            due_date: day,
        });
        assert_eq!(
            first.account().unwrap().account.cash.to_string(),
            "100.000000000000"
        );
        assert!(first.account().unwrap().account.pending_cash.is_empty());
        assert_eq!(
            second.account().unwrap().account.cash.to_string(),
            "200.000000000000"
        );
    }
}
