//! Owned account projections; none of these values provides a mutable ledger handle.
use std::fmt;

use chrono::NaiveDate;
use snafu::Snafu;

use crate::decimal::{Money, Nav, Shares};

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct Code(String);

#[derive(Debug, Snafu, PartialEq, Eq)]
#[snafu(display("Fund code must contain exactly six ASCII digits"))]
pub struct CodeError;

impl Code {
    pub fn parse(input: &str) -> Result<Self, CodeError> {
        if input.len() != 6 || !input.bytes().all(|b| b.is_ascii_digit()) {
            return Err(CodeError);
        }
        Ok(Self(input.to_owned()))
    }
}

impl fmt::Display for Code {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Buy,
    Sell,
}

#[derive(Debug, Snafu, PartialEq, Eq)]
pub enum OrderError {
    #[snafu(display("Order shares must be strictly positive"))]
    NonPositive,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Order {
    pub code: Code,
    pub side: Side,
    pub shares: Shares,
}

impl Order {
    pub fn new(code: Code, side: Side, shares: Shares) -> Result<Self, OrderError> {
        if shares == Shares::ZERO {
            return Err(OrderError::NonPositive);
        }
        Ok(Self { code, side, shares })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Holding {
    pub code: Code,
    pub shares: Shares,
    pub unit_nav: Nav,
    pub price_date: NaiveDate,
    pub stale: bool,
    pub value: Money,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PendingBuy {
    pub code: Code,
    pub shares: Shares,
    pub execution_date: NaiveDate,
    pub due_date: NaiveDate,
    pub unit_nav: Nav,
    pub price_date: NaiveDate,
    pub stale: bool,
    pub value: Money,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PendingCash {
    pub code: Code,
    pub amount: Money,
    pub execution_date: NaiveDate,
    pub due_date: NaiveDate,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Account {
    pub valuation_date: NaiveDate,
    pub cash: Money,
    pub holdings: Vec<Holding>,
    pub pending_buys: Vec<PendingBuy>,
    pub pending_cash: Vec<PendingCash>,
    pub confirmed_value: Money,
    pub pending_buy_value: Money,
    pub pending_cash_value: Money,
    pub total_assets: Money,
}

impl Account {
    pub(crate) fn empty(date: NaiveDate, cash: Money) -> Self {
        Self {
            valuation_date: date,
            cash,
            holdings: Vec::new(),
            pending_buys: Vec::new(),
            pending_cash: Vec::new(),
            confirmed_value: Money::ZERO,
            pending_buy_value: Money::ZERO,
            pending_cash_value: Money::ZERO,
            total_assets: cash,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn orders_require_positive_shares() {
        let code = Code::parse("000001").unwrap();
        assert_eq!(
            Order::new(code.clone(), Side::Buy, Shares::ZERO),
            Err(OrderError::NonPositive)
        );
        let shares = Shares::parse_positive("0.000001").unwrap();
        assert_eq!(
            Order::new(code.clone(), Side::Sell, shares),
            Ok(Order {
                code,
                side: Side::Sell,
                shares,
            })
        );
    }

    #[test]
    fn six_ascii_digit_codes_only() {
        assert_eq!(Code::parse("000001").unwrap().to_string(), "000001");
        for input in [
            "",
            "12345",
            "1234567",
            "１２３４５６",
            "١٢٣٤٥٦",
            "12345a",
            " 12345",
        ] {
            assert_eq!(Code::parse(input), Err(CodeError));
        }
    }
}
