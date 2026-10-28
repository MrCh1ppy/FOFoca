use chrono::{Datelike, NaiveDate};
use snafu::Snafu;

use crate::{
    decimal::{DecimalError, Money},
    market::{MarketError, MarketSnapshot},
    model::{Account, Order},
    settlement::{self, Execution, Issue, SettlementOutcome, SettlementRejection, Skip},
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
    Finished,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Advanced {
    pub closed_date: NaiveDate,
    pub executions: Vec<Execution>,
    pub skips: Vec<Skip>,
    pub account: Account,
    pub finished: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AdvanceOutcome {
    Advanced(Advanced),
    Rejected(SettlementRejection),
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
    pub account: Account,
}

#[derive(Default)]
pub struct Session {
    state: Option<Snapshot>,
    market_attempt: Option<MarketSnapshot>,
}

#[derive(Debug, Snafu, PartialEq, Eq)]
pub enum SessionError {
    #[snafu(display("This business operation is not implemented"))]
    NotImplemented,
    #[snafu(display("Session is not initialized"))]
    NotInitialized,
    #[snafu(display("Session is already initialized"))]
    AlreadyInitialized,
    #[snafu(display("Session is finished"))]
    Finished,
    #[snafu(display("Invalid market: {source}"))]
    Market { source: MarketError },
    #[snafu(display("Market conflicts with the pinned snapshot"))]
    MarketConflict,
}

impl Session {
    /// Orders are borrowed per call and never retained, even after rejection.
    /// A valid market is pinned until the day successfully closes.
    pub fn advance_day(
        &mut self,
        orders: &[Order],
        market: &MarketSnapshot,
    ) -> Result<AdvanceOutcome, SessionError> {
        let state = self.state.as_ref().ok_or(SessionError::NotInitialized)?;
        if state.status == Status::Finished {
            return Err(SessionError::Finished);
        }
        let day = state.current_date;
        if market.date() != day {
            return Err(SessionError::Market {
                source: MarketError::WrongDate {
                    expected: day,
                    actual: market.date(),
                },
            });
        }
        if let Some(pinned) = &self.market_attempt {
            if pinned != market {
                return Err(SessionError::MarketConflict);
            }
        } else {
            self.market_attempt = Some(market.clone());
        }

        let mut opening = state.account.clone();
        let outcome = if crate::arrival::arrive(&mut opening, day).is_err() {
            let (nets, mut issues) = settlement::derive_nets(orders);
            issues.push(Issue::ArithmeticOverflow { code: None });
            SettlementOutcome::Rejected(SettlementRejection {
                orders: orders.to_vec(),
                nets,
                skips: Vec::new(),
                issues,
            })
        } else {
            settlement::settle(&opening, day, market.unit_nav(), orders)
        };
        match outcome {
            SettlementOutcome::Rejected(rejection) => Ok(AdvanceOutcome::Rejected(rejection)),
            SettlementOutcome::Candidate(candidate) => {
                let finished = day == state.end_date;
                let mut next = state.clone();
                next.account = candidate.account.clone();
                next.last_closed_date = Some(day);
                next.snapshot_pinned = false;
                if finished {
                    next.status = Status::Finished;
                } else {
                    // Config's bounded inclusive end date guarantees this successor.
                    next.current_date = day.succ_opt().expect("nonterminal date has a successor");
                }
                self.state = Some(next);
                self.market_attempt = None;
                Ok(AdvanceOutcome::Advanced(Advanced {
                    closed_date: day,
                    executions: candidate.executions,
                    skips: candidate.skips,
                    account: candidate.account,
                    finished,
                }))
            }
        }
    }

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
        let mut snapshot = self.state.clone().ok_or(SessionError::NotInitialized)?;
        snapshot.snapshot_pinned = self.market_attempt.is_some();
        Ok(snapshot)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        decimal::{Nav, Shares},
        model::{Code, Holding, PendingBuy, PendingCash},
    };
    use std::collections::BTreeMap;

    #[test]
    fn checked_arrival_failure_discards_working_copy() {
        let day = date(2026, 1, 2);
        let mut session = Session::default();
        session
            .initialize(Config::parse(day, day, "79228162514264337.593543950335").unwrap())
            .unwrap();
        // Synthetic ledger boundary: a due cash arrival cannot fit available cash.
        session
            .state
            .as_mut()
            .unwrap()
            .account
            .pending_cash
            .push(PendingCash {
                code: Code::parse("000001").unwrap(),
                amount: Money::parse("1").unwrap(),
                execution_date: day.pred_opt().unwrap(),
                due_date: day,
            });
        let before = session.account().unwrap();
        let market = MarketSnapshot::new(day, BTreeMap::new()).unwrap();
        assert!(matches!(session.advance_day(&[], &market).unwrap(),
            AdvanceOutcome::Rejected(SettlementRejection { issues, .. })
            if issues == vec![Issue::ArithmeticOverflow { code: None }]));
        let after = session.account().unwrap();
        assert_eq!(after.account, before.account);
        assert_eq!(after.current_date, before.current_date);
        assert_eq!(after.last_closed_date, None);
        assert!(after.snapshot_pinned);
    }

    #[test]
    fn overdue_buys_merge_and_future_arrivals_remain() {
        let day = date(2026, 1, 3);
        let prior = date(2026, 1, 1);
        let mut session = Session::default();
        session
            .initialize(Config::parse(day, date(2026, 1, 4), "0").unwrap())
            .unwrap();
        let account = &mut session.state.as_mut().unwrap().account;
        account.holdings.push(Holding {
            code: Code::parse("000001").unwrap(),
            shares: Shares::parse("1").unwrap(),
            unit_nav: Nav::parse("2").unwrap(),
            price_date: prior,
            stale: false,
            value: Money::parse("2").unwrap(),
        });
        for due_date in [date(2026, 1, 2), day, date(2026, 1, 5)] {
            account.pending_buys.push(PendingBuy {
                code: Code::parse("000001").unwrap(),
                shares: Shares::parse("1").unwrap(),
                execution_date: prior,
                due_date,
                unit_nav: Nav::parse("2").unwrap(),
                price_date: prior,
                stale: false,
                value: Money::parse("2").unwrap(),
            });
        }
        for current in [day, date(2026, 1, 4)] {
            let result = session
                .advance_day(&[], &MarketSnapshot::new(current, BTreeMap::new()).unwrap())
                .unwrap();
            let AdvanceOutcome::Advanced(close) = result else {
                panic!("arrival failed")
            };
            assert_eq!(close.account.holdings.len(), 1);
            assert_eq!(
                close.account.holdings[0].shares,
                Shares::parse("3").unwrap()
            );
            assert_eq!(close.account.pending_buys.len(), 1);
            assert_eq!(close.account.total_assets, Money::parse("8").unwrap());
        }
    }

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
