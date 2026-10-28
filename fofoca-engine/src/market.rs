//! Validated, normalized closing prices for one natural date.
use std::collections::BTreeMap;

use chrono::{Datelike, NaiveDate};
use snafu::Snafu;

use crate::{decimal::Nav, model::Code};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MarketSnapshot {
    date: NaiveDate,
    unit_nav: BTreeMap<Code, Option<Nav>>,
}

#[derive(Debug, Snafu, PartialEq, Eq)]
pub enum MarketError {
    #[snafu(display("Market date must be in years 0001 through 9999"))]
    DateRange,
    #[snafu(display("Market date {actual} does not match current date {expected}"))]
    WrongDate {
        expected: NaiveDate,
        actual: NaiveDate,
    },
}

impl MarketSnapshot {
    /// Code and NAV validity is enforced by their domain constructors.
    /// Explicit missing values are removed, making missing keys equivalent to None.
    pub fn new(
        date: NaiveDate,
        mut unit_nav: BTreeMap<Code, Option<Nav>>,
    ) -> Result<Self, MarketError> {
        if !(1..=9999).contains(&date.year()) {
            return Err(MarketError::DateRange);
        }
        unit_nav.retain(|_, nav| nav.is_some());
        Ok(Self { date, unit_nav })
    }

    pub fn date(&self) -> NaiveDate {
        self.date
    }

    pub fn unit_nav(&self) -> &BTreeMap<Code, Option<Nav>> {
        &self.unit_nav
    }
}
