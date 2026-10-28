//! Stateless single-day settlement candidate calculation.
//!
//! The opening account is borrowed and never mutated. A successful outcome owns a
//! candidate account plus the day's executions; a rejected
//! outcome owns the original orders, derived nets, provisional skips, and every
//! independently discoverable issue. Calendar advancement, arrivals, price
//! caching, records, and session commits remain elsewhere.
use std::{cmp::Ordering, collections::BTreeMap};

use chrono::{Datelike, NaiveDate};

use crate::{
    decimal::{DecimalError, Money, Nav, Shares},
    model::{Account, Code, Order, PendingBuy, PendingCash, Side},
};

/// Derived signed net shares; the magnitude of a non-zero net is always positive.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NetShares {
    Buy(Shares),
    Sell(Shares),
    NoTrade,
}

impl NetShares {
    pub fn side(self) -> Option<Side> {
        match self {
            Self::Buy(_) => Some(Side::Buy),
            Self::Sell(_) => Some(Side::Sell),
            Self::NoTrade => None,
        }
    }

    pub fn shares(self) -> Shares {
        match self {
            Self::Buy(shares) | Self::Sell(shares) => shares,
            Self::NoTrade => Shares::ZERO,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DerivedNet {
    pub code: Code,
    pub net: NetShares,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkipReason {
    MissingNav,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Skip {
    pub code: Code,
    pub net: NetShares,
    pub reason: SkipReason,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Execution {
    pub code: Code,
    pub side: Side,
    pub shares: Shares,
    pub unit_nav: Nav,
    pub amount: Money,
    pub execution_date: NaiveDate,
    pub due_date: NaiveDate,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Issue {
    MissingClosingNav {
        code: Code,
    },
    Oversell {
        code: Code,
        requested: Shares,
        confirmed: Shares,
    },
    InsufficientCash {
        required: Money,
        available: Money,
    },
    ArithmeticOverflow {
        code: Option<Code>,
    },
    DueDateOverflow,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SettlementCandidate {
    pub account: Account,
    pub executions: Vec<Execution>,
    pub skips: Vec<Skip>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SettlementRejection {
    pub orders: Vec<Order>,
    pub nets: Vec<DerivedNet>,
    pub skips: Vec<Skip>,
    pub issues: Vec<Issue>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SettlementOutcome {
    Candidate(SettlementCandidate),
    Rejected(SettlementRejection),
}

impl SettlementOutcome {
    pub fn candidate(&self) -> Option<&SettlementCandidate> {
        match self {
            Self::Candidate(candidate) => Some(candidate),
            Self::Rejected(_) => None,
        }
    }

    pub fn rejection(&self) -> Option<&SettlementRejection> {
        match self {
            Self::Candidate(_) => None,
            Self::Rejected(rejection) => Some(rejection),
        }
    }
}

struct PendingOp {
    code: Code,
    side: Side,
    shares: Shares,
    nav: Nav,
    amount: Option<Money>,
}

/// Compute one day's closing candidate from the opening account.
///
/// `unit_nav` maps fund codes to the current day's optional unit NAV; a missing
/// key and `None` are equivalent. Non-zero nets without a price reject the batch
/// before any cash or confirmed-share check for that code. Executable sells
/// are checked against opening confirmed shares and all executable buy costs are
/// checked once against opening cash. Any issue rejects the whole batch.
pub fn settle(
    opening: &Account,
    current_date: NaiveDate,
    unit_nav: &BTreeMap<Code, Option<Nav>>,
    orders: &[Order],
) -> SettlementOutcome {
    let (nets, mut issues) = derive_nets(orders);
    let mut skips = Vec::new();
    let mut ops = Vec::new();

    for derived in &nets {
        let (side, shares) = match derived.net {
            NetShares::Buy(shares) => (Side::Buy, shares),
            NetShares::Sell(shares) => (Side::Sell, shares),
            NetShares::NoTrade => continue,
        };
        let Some(nav) = unit_nav.get(&derived.code).and_then(|nav| *nav) else {
            issues.push(Issue::MissingClosingNav {
                code: derived.code.clone(),
            });
            skips.push(Skip {
                code: derived.code.clone(),
                net: derived.net,
                reason: SkipReason::MissingNav,
            });
            continue;
        };
        let amount = match shares.checked_value(nav) {
            Ok(amount) => Some(amount),
            Err(_) => {
                issues.push(Issue::ArithmeticOverflow {
                    code: Some(derived.code.clone()),
                });
                None
            }
        };
        ops.push(PendingOp {
            code: derived.code.clone(),
            side,
            shares,
            nav,
            amount,
        });
    }

    let due_date = current_date
        .succ_opt()
        .filter(|date| (1..=9999).contains(&date.year()));
    if !ops.is_empty() && due_date.is_none() {
        issues.push(Issue::DueDateOverflow);
    }

    for op in ops.iter().filter(|op| op.side == Side::Sell) {
        let confirmed = opening
            .holdings
            .iter()
            .find(|holding| holding.code == op.code)
            .map_or(Shares::ZERO, |holding| holding.shares);
        if op.shares > confirmed {
            issues.push(Issue::Oversell {
                code: op.code.clone(),
                requested: op.shares,
                confirmed,
            });
        }
    }

    let mut total_buy = Money::ZERO;
    let mut buy_total_complete = true;
    for op in ops.iter().filter(|op| op.side == Side::Buy) {
        let Some(cost) = op.amount else {
            buy_total_complete = false;
            continue;
        };
        if buy_total_complete {
            match total_buy.checked_add(cost) {
                Ok(total) => total_buy = total,
                Err(_) => {
                    issues.push(Issue::ArithmeticOverflow { code: None });
                    buy_total_complete = false;
                }
            }
        }
    }
    if buy_total_complete && total_buy > opening.cash {
        issues.push(Issue::InsufficientCash {
            required: total_buy,
            available: opening.cash,
        });
    }

    if !issues.is_empty() {
        return SettlementOutcome::Rejected(SettlementRejection {
            orders: orders.to_vec(),
            nets,
            skips,
            issues,
        });
    }

    // A zero-execution close needs no due date. Otherwise the earlier check has
    // already rejected an unrepresentable D+1, so this fallback is defensive only.
    let due_date = match due_date {
        Some(due_date) => due_date,
        None if ops.is_empty() => current_date,
        None => {
            issues.push(Issue::DueDateOverflow);
            return SettlementOutcome::Rejected(SettlementRejection {
                orders: orders.to_vec(),
                nets,
                skips,
                issues,
            });
        }
    };

    build_candidate(
        opening,
        current_date,
        due_date,
        unit_nav,
        nets,
        skips,
        ops,
        orders,
    )
}

#[allow(clippy::too_many_arguments)]
fn build_candidate(
    opening: &Account,
    current_date: NaiveDate,
    due_date: NaiveDate,
    unit_nav: &BTreeMap<Code, Option<Nav>>,
    nets: Vec<DerivedNet>,
    skips: Vec<Skip>,
    ops: Vec<PendingOp>,
    orders: &[Order],
) -> SettlementOutcome {
    let mut account = opening.clone();
    account.valuation_date = current_date;
    let mut executions = Vec::with_capacity(ops.len());
    let mut issues = Vec::new();

    for op in &ops {
        let Some(amount) = op.amount else {
            issues.push(Issue::ArithmeticOverflow {
                code: Some(op.code.clone()),
            });
            continue;
        };
        executions.push(Execution {
            code: op.code.clone(),
            side: op.side,
            shares: op.shares,
            unit_nav: op.nav,
            amount,
            execution_date: current_date,
            due_date,
        });

        match op.side {
            Side::Buy => match account.cash.checked_sub(amount) {
                Ok(cash) => {
                    account.cash = cash;
                    account.pending_buys.push(PendingBuy {
                        code: op.code.clone(),
                        shares: op.shares,
                        execution_date: current_date,
                        due_date,
                        unit_nav: op.nav,
                        price_date: current_date,
                        stale: false,
                        value: amount,
                    });
                }
                Err(_) => issues.push(Issue::ArithmeticOverflow {
                    code: Some(op.code.clone()),
                }),
            },
            Side::Sell => {
                let Some(index) = account
                    .holdings
                    .iter()
                    .position(|holding| holding.code == op.code)
                else {
                    issues.push(Issue::Oversell {
                        code: op.code.clone(),
                        requested: op.shares,
                        confirmed: Shares::ZERO,
                    });
                    continue;
                };
                match account.holdings[index].shares.checked_sub(op.shares) {
                    Ok(shares) => account.holdings[index].shares = shares,
                    Err(_) => issues.push(Issue::Oversell {
                        code: op.code.clone(),
                        requested: op.shares,
                        confirmed: account.holdings[index].shares,
                    }),
                }
                account.pending_cash.push(PendingCash {
                    code: op.code.clone(),
                    amount,
                    execution_date: current_date,
                    due_date,
                });
            }
        }
    }
    account
        .holdings
        .retain(|holding| holding.shares != Shares::ZERO);

    if let Err(()) = revalue_account(&mut account, current_date, unit_nav) {
        issues.push(Issue::ArithmeticOverflow { code: None });
    }

    if issues.is_empty() {
        SettlementOutcome::Candidate(SettlementCandidate {
            account,
            executions,
            skips,
        })
    } else {
        SettlementOutcome::Rejected(SettlementRejection {
            orders: orders.to_vec(),
            nets,
            skips,
            issues,
        })
    }
}

fn revalue_account(
    account: &mut Account,
    current_date: NaiveDate,
    unit_nav: &BTreeMap<Code, Option<Nav>>,
) -> Result<(), ()> {
    for holding in &mut account.holdings {
        let (nav, price_date, stale) = valuation_price(
            &holding.code,
            holding.unit_nav,
            holding.price_date,
            current_date,
            unit_nav,
        );
        holding.unit_nav = nav;
        holding.price_date = price_date;
        holding.stale = stale;
        holding.value = holding.shares.checked_value(nav).map_err(|_| ())?;
    }
    for pending in &mut account.pending_buys {
        let (nav, price_date, stale) = valuation_price(
            &pending.code,
            pending.unit_nav,
            pending.price_date,
            current_date,
            unit_nav,
        );
        pending.unit_nav = nav;
        pending.price_date = price_date;
        pending.stale = stale;
        pending.value = pending.shares.checked_value(nav).map_err(|_| ())?;
    }

    account.confirmed_value = sum_money(account.holdings.iter().map(|holding| holding.value))?;
    account.pending_buy_value =
        sum_money(account.pending_buys.iter().map(|pending| pending.value))?;
    account.pending_cash_value =
        sum_money(account.pending_cash.iter().map(|pending| pending.amount))?;
    account.total_assets = account
        .cash
        .checked_add(account.confirmed_value)
        .map_err(|_| ())?
        .checked_add(account.pending_buy_value)
        .map_err(|_| ())?
        .checked_add(account.pending_cash_value)
        .map_err(|_| ())?;
    Ok(())
}

fn valuation_price(
    code: &Code,
    stored_nav: Nav,
    stored_price_date: NaiveDate,
    current_date: NaiveDate,
    unit_nav: &BTreeMap<Code, Option<Nav>>,
) -> (Nav, NaiveDate, bool) {
    match unit_nav.get(code).and_then(|nav| *nav) {
        Some(nav) => (nav, current_date, false),
        None => (
            stored_nav,
            stored_price_date,
            stored_price_date < current_date,
        ),
    }
}

fn sum_money(mut values: impl Iterator<Item = Money>) -> Result<Money, ()> {
    values.try_fold(Money::ZERO, |total, value| {
        total.checked_add(value).map_err(|_| ())
    })
}

pub(crate) fn derive_nets(orders: &[Order]) -> (Vec<DerivedNet>, Vec<Issue>) {
    let mut nets = BTreeMap::new();
    let mut issues = Vec::new();
    for order in orders {
        let current = nets.get(&order.code).copied().unwrap_or(NetShares::NoTrade);
        let next = match order.side {
            Side::Buy => add_buy(current, order.shares),
            Side::Sell => add_sell(current, order.shares),
        };
        match next {
            Ok(net) => {
                nets.insert(order.code.clone(), net);
            }
            Err(_) => issues.push(Issue::ArithmeticOverflow {
                code: Some(order.code.clone()),
            }),
        }
    }
    let nets = nets
        .into_iter()
        .map(|(code, net)| DerivedNet { code, net })
        .collect();
    (nets, issues)
}

fn add_buy(current: NetShares, shares: Shares) -> Result<NetShares, DecimalError> {
    match current {
        NetShares::NoTrade => Ok(NetShares::Buy(shares)),
        NetShares::Buy(existing) => existing.checked_add(shares).map(NetShares::Buy),
        NetShares::Sell(existing) => match shares.cmp(&existing) {
            Ordering::Less => existing.checked_sub(shares).map(NetShares::Sell),
            Ordering::Equal => Ok(NetShares::NoTrade),
            Ordering::Greater => shares.checked_sub(existing).map(NetShares::Buy),
        },
    }
}

fn add_sell(current: NetShares, shares: Shares) -> Result<NetShares, DecimalError> {
    match current {
        NetShares::NoTrade => Ok(NetShares::Sell(shares)),
        NetShares::Sell(existing) => existing.checked_add(shares).map(NetShares::Sell),
        NetShares::Buy(existing) => match shares.cmp(&existing) {
            Ordering::Less => existing.checked_sub(shares).map(NetShares::Buy),
            Ordering::Equal => Ok(NetShares::NoTrade),
            Ordering::Greater => shares.checked_sub(existing).map(NetShares::Sell),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Holding, PendingBuy, PendingCash};

    fn date(year: i32, month: u32, day: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(year, month, day).unwrap()
    }

    fn code(input: &str) -> Code {
        Code::parse(input).unwrap()
    }

    fn money(input: &str) -> Money {
        Money::parse(input).unwrap()
    }

    fn shares(input: &str) -> Shares {
        Shares::parse(input).unwrap()
    }

    fn nav(input: &str) -> Nav {
        Nav::parse(input).unwrap()
    }

    fn order(fund: &str, side: Side, quantity: &str) -> Order {
        Order::new(code(fund), side, shares(quantity)).unwrap()
    }

    fn market(entries: &[(&str, Option<&str>)]) -> BTreeMap<Code, Option<Nav>> {
        entries
            .iter()
            .map(|(fund, value)| (code(fund), value.map(nav)))
            .collect()
    }

    fn holding(fund: &str, quantity: &str, price: &str, price_date: NaiveDate) -> Holding {
        let quantity = shares(quantity);
        let price = nav(price);
        Holding {
            code: code(fund),
            shares: quantity,
            unit_nav: price,
            price_date,
            stale: false,
            value: quantity.checked_value(price).unwrap(),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn pending_buy(
        fund: &str,
        quantity: &str,
        price: &str,
        execution_date: NaiveDate,
        due_date: NaiveDate,
    ) -> PendingBuy {
        let quantity = shares(quantity);
        let price = nav(price);
        PendingBuy {
            code: code(fund),
            shares: quantity,
            execution_date,
            due_date,
            unit_nav: price,
            price_date: execution_date,
            stale: false,
            value: quantity.checked_value(price).unwrap(),
        }
    }

    fn pending_cash(
        fund: &str,
        amount: &str,
        execution_date: NaiveDate,
        due_date: NaiveDate,
    ) -> PendingCash {
        PendingCash {
            code: code(fund),
            amount: money(amount),
            execution_date,
            due_date,
        }
    }

    fn account(
        valuation_date: NaiveDate,
        cash: Money,
        holdings: Vec<Holding>,
        pending_buys: Vec<PendingBuy>,
        pending_cash: Vec<PendingCash>,
    ) -> Account {
        let confirmed_value = sum_money(holdings.iter().map(|holding| holding.value)).unwrap();
        let pending_buy_value =
            sum_money(pending_buys.iter().map(|pending| pending.value)).unwrap();
        let pending_cash_value =
            sum_money(pending_cash.iter().map(|pending| pending.amount)).unwrap();
        let total_assets = cash
            .checked_add(confirmed_value)
            .unwrap()
            .checked_add(pending_buy_value)
            .unwrap()
            .checked_add(pending_cash_value)
            .unwrap();
        Account {
            valuation_date,
            cash,
            holdings,
            pending_buys,
            pending_cash,
            confirmed_value,
            pending_buy_value,
            pending_cash_value,
            total_assets,
        }
    }

    #[test]
    fn buys_and_sells_net_to_one_execution() {
        let day = date(2026, 1, 1);
        let opening = account(
            day,
            money("100"),
            vec![holding("000001", "3", "2", day)],
            Vec::new(),
            Vec::new(),
        );
        let orders = vec![
            order("000001", Side::Buy, "3"),
            order("000001", Side::Sell, "6"),
        ];
        let outcome = settle(&opening, day, &market(&[("000001", Some("3"))]), &orders);
        let candidate = outcome.candidate().unwrap();

        assert_eq!(candidate.executions.len(), 1);
        assert_eq!(candidate.executions[0].side, Side::Sell);
        assert_eq!(candidate.executions[0].shares, shares("3"));
        assert_eq!(candidate.executions[0].amount, money("9"));
        assert_eq!(candidate.executions[0].due_date, date(2026, 1, 2));
        assert!(candidate.account.holdings.is_empty());
        assert_eq!(candidate.account.cash, money("100"));
        assert_eq!(candidate.account.pending_cash.len(), 1);
        assert_eq!(candidate.account.pending_cash[0].amount, money("9"));
        assert_eq!(candidate.account.total_assets, money("109"));
        assert!(candidate.skips.is_empty());
    }

    #[test]
    fn zero_net_needs_no_nav_and_does_nothing() {
        let day = date(2026, 1, 1);
        let opening = account(day, money("100"), Vec::new(), Vec::new(), Vec::new());
        let orders = vec![
            order("000001", Side::Buy, "1.25"),
            order("000001", Side::Sell, "1.25"),
        ];
        let outcome = settle(&opening, day, &BTreeMap::new(), &orders);
        let candidate = outcome.candidate().unwrap();

        assert!(candidate.executions.is_empty());
        assert!(candidate.skips.is_empty());
        assert_eq!(candidate.account, opening);
    }

    #[test]
    fn missing_nav_rejects_before_oversell_and_cash_checks() {
        let day = date(2026, 1, 1);
        let opening = account(day, money("100"), Vec::new(), Vec::new(), Vec::new());
        let orders = vec![
            order("000001", Side::Sell, "1"),
            order("000002", Side::Buy, "1000000"),
        ];
        let outcome = settle(&opening, day, &BTreeMap::new(), &orders);
        let rejection = outcome.rejection().unwrap();

        assert_eq!(
            rejection.issues,
            vec![
                Issue::MissingClosingNav {
                    code: code("000001")
                },
                Issue::MissingClosingNav {
                    code: code("000002")
                },
            ]
        );
        assert_eq!(rejection.skips.len(), 2);
        assert_eq!(rejection.skips[0].code, code("000001"));
        assert_eq!(rejection.skips[0].net, NetShares::Sell(shares("1")));
        assert_eq!(rejection.skips[1].code, code("000002"));
        assert_eq!(rejection.skips[1].net, NetShares::Buy(shares("1000000")));
        assert!(
            rejection
                .skips
                .iter()
                .all(|skip| skip.reason == SkipReason::MissingNav)
        );
        assert_eq!(opening.cash, money("100"));
    }

    #[test]
    fn zero_net_is_reported_as_no_trade_when_another_net_rejects() {
        let day = date(2026, 1, 1);
        let opening = account(day, money("1"), Vec::new(), Vec::new(), Vec::new());
        let orders = vec![
            order("000001", Side::Buy, "1.25"),
            order("000001", Side::Sell, "1.25"),
            order("000002", Side::Buy, "1"),
        ];
        let outcome = settle(&opening, day, &market(&[("000002", Some("10"))]), &orders);
        let rejection = outcome.rejection().unwrap();

        assert_eq!(
            rejection.nets,
            vec![
                DerivedNet {
                    code: code("000001"),
                    net: NetShares::NoTrade,
                },
                DerivedNet {
                    code: code("000002"),
                    net: NetShares::Buy(shares("1")),
                },
            ]
        );
        assert!(rejection.skips.is_empty());
        assert!(rejection.issues.contains(&Issue::InsufficientCash {
            required: money("10"),
            available: money("1"),
        }));
    }

    #[test]
    fn aggregate_buy_cash_never_recycles_sale_proceeds() {
        let day = date(2026, 1, 1);
        let opening = account(
            day,
            money("10"),
            vec![holding("000003", "10", "1", day)],
            Vec::new(),
            Vec::new(),
        );
        let prices = market(&[
            ("000001", Some("2")),
            ("000002", Some("2")),
            ("000003", Some("2")),
        ]);
        let orders = vec![
            order("000003", Side::Sell, "10"),
            order("000002", Side::Buy, "3"),
            order("000001", Side::Buy, "3"),
        ];
        let outcome = settle(&opening, day, &prices, &orders);
        let rejection = outcome.rejection().unwrap();

        assert!(rejection.issues.contains(&Issue::InsufficientCash {
            required: money("12"),
            available: money("10"),
        }));
        assert!(
            !rejection
                .issues
                .iter()
                .any(|issue| matches!(issue, Issue::Oversell { .. }))
        );
        assert_eq!(rejection.orders, orders);
        assert_eq!(
            rejection.nets,
            vec![
                DerivedNet {
                    code: code("000001"),
                    net: NetShares::Buy(shares("3")),
                },
                DerivedNet {
                    code: code("000002"),
                    net: NetShares::Buy(shares("3")),
                },
                DerivedNet {
                    code: code("000003"),
                    net: NetShares::Sell(shares("10")),
                },
            ]
        );
    }

    #[test]
    fn atomic_rejection_collects_independent_issues_without_mutation() {
        let day = date(2026, 1, 1);
        let opening = account(
            day,
            money("10"),
            vec![holding("000001", "1", "1", day)],
            Vec::new(),
            Vec::new(),
        );
        let before = opening.clone();
        let prices = market(&[("000001", Some("1")), ("000002", Some("10"))]);
        let orders = vec![
            order("000001", Side::Sell, "2"),
            order("000002", Side::Buy, "2"),
        ];
        let outcome = settle(&opening, day, &prices, &orders);
        let rejection = outcome.rejection().unwrap();

        assert!(rejection.issues.contains(&Issue::Oversell {
            code: code("000001"),
            requested: shares("2"),
            confirmed: shares("1"),
        }));
        assert!(rejection.issues.contains(&Issue::InsufficientCash {
            required: money("20"),
            available: money("10"),
        }));
        assert_eq!(opening, before);
    }

    #[test]
    fn fractional_buy_uses_exact_product_and_due_date() {
        let day = date(2026, 1, 1);
        let opening = account(day, money("1"), Vec::new(), Vec::new(), Vec::new());
        let orders = vec![order("123456", Side::Buy, "0.123456")];
        let outcome = settle(
            &opening,
            day,
            &market(&[("123456", Some("1.234567"))]),
            &orders,
        );
        let candidate = outcome.candidate().unwrap();
        let pending = &candidate.account.pending_buys[0];

        assert_eq!(candidate.executions[0].amount.to_string(), "0.152414703552");
        assert_eq!(candidate.executions[0].due_date, date(2026, 1, 2));
        assert_eq!(pending.code, code("123456"));
        assert_eq!(pending.shares, shares("0.123456"));
        assert_eq!(pending.unit_nav, nav("1.234567"));
        assert_eq!(pending.value.to_string(), "0.152414703552");
        assert_eq!(pending.execution_date, day);
        assert_eq!(pending.due_date, date(2026, 1, 2));
        assert_eq!(candidate.account.cash.to_string(), "0.847585296448");
        assert_eq!(candidate.account.total_assets.to_string(), "1.000000000000");
    }

    #[test]
    fn unrepresentable_due_date_rejects_but_empty_close_succeeds() {
        let day = date(9999, 12, 31);
        let opening = account(day, money("1"), Vec::new(), Vec::new(), Vec::new());

        let outcome = settle(
            &opening,
            day,
            &market(&[("000001", Some("1"))]),
            &[order("000001", Side::Buy, "1")],
        );
        assert!(
            outcome
                .rejection()
                .unwrap()
                .issues
                .contains(&Issue::DueDateOverflow)
        );

        let outcome = settle(&opening, day, &BTreeMap::new(), &[]);
        assert!(outcome.candidate().is_some());
    }

    #[test]
    fn existing_assets_revalue_from_candidate_or_stored_price() {
        let price_date = date(2026, 1, 1);
        let day = date(2026, 1, 3);
        let opening = account(
            day,
            money("0"),
            vec![holding("000001", "2", "2", price_date)],
            vec![pending_buy(
                "000002",
                "3",
                "4",
                price_date,
                date(2026, 1, 2),
            )],
            vec![pending_cash("000003", "5", price_date, date(2026, 1, 2))],
        );

        let outcome = settle(&opening, day, &BTreeMap::new(), &[]);
        let candidate = outcome.candidate().unwrap();
        assert_eq!(candidate.account.holdings[0].value, money("4"));
        assert!(candidate.account.holdings[0].stale);
        assert_eq!(candidate.account.holdings[0].price_date, price_date);
        assert_eq!(candidate.account.pending_buys[0].value, money("12"));
        assert!(candidate.account.pending_buys[0].stale);
        assert_eq!(candidate.account.confirmed_value, money("4"));
        assert_eq!(candidate.account.pending_buy_value, money("12"));
        assert_eq!(candidate.account.pending_cash_value, money("5"));
        assert_eq!(candidate.account.total_assets, money("21"));

        let prices = market(&[("000001", Some("3")), ("000002", Some("5"))]);
        let outcome = settle(&opening, day, &prices, &[]);
        let candidate = outcome.candidate().unwrap();
        assert_eq!(candidate.account.holdings[0].value, money("6"));
        assert!(!candidate.account.holdings[0].stale);
        assert_eq!(candidate.account.holdings[0].price_date, day);
        assert_eq!(candidate.account.pending_buys[0].value, money("15"));
        assert!(!candidate.account.pending_buys[0].stale);
        assert_eq!(candidate.account.total_assets, money("26"));
    }
}
