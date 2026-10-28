use std::collections::BTreeMap;

use chrono::NaiveDate;
use fofoca_engine::{
    AdvanceOutcome, Advanced, Config, MarketError, MarketSnapshot, Session, SessionError, Status,
    decimal::{Money, Nav, Shares},
    model::{Code, Order, Side},
    settlement::{Issue, SettlementRejection},
};

fn date(day: u32) -> NaiveDate {
    NaiveDate::from_ymd_opt(2026, 1, day).unwrap()
}

fn code(value: &str) -> Code {
    Code::parse(value).unwrap()
}

fn money(value: &str) -> Money {
    Money::parse(value).unwrap()
}

fn order(fund: &str, side: Side, shares: &str) -> Order {
    Order::new(code(fund), side, Shares::parse_positive(shares).unwrap()).unwrap()
}

fn market(day: NaiveDate, entries: &[(&str, Option<&str>)]) -> MarketSnapshot {
    MarketSnapshot::new(
        day,
        entries
            .iter()
            .map(|(code_, nav)| (code(code_), nav.map(|v| Nav::parse(v).unwrap())))
            .collect(),
    )
    .unwrap()
}

fn session(start: NaiveDate, end: NaiveDate, cash: &str) -> Session {
    let mut session = Session::default();
    session
        .initialize(Config::parse(start, end, cash).unwrap())
        .unwrap();
    session
}

fn advanced(outcome: AdvanceOutcome) -> Advanced {
    match outcome {
        AdvanceOutcome::Advanced(result) => result,
        other => panic!("expected advance: {other:?}"),
    }
}

fn rejected(outcome: AdvanceOutcome) -> SettlementRejection {
    match outcome {
        AdvanceOutcome::Rejected(result) => result,
        other => panic!("expected rejection: {other:?}"),
    }
}

#[test]
fn per_call_orders_pin_equal_retry_conflict_and_detached_outcomes() {
    let mut s = session(date(1), date(3), "10");
    let prices = market(date(1), &[("000002", Some("3")), ("000001", Some("2"))]);
    let equal = market(
        date(1),
        &[
            ("000003", None),
            ("000001", Some("2.000000")),
            ("000002", Some("3.0")),
        ],
    );
    assert_eq!(prices, equal);
    let original = vec![order("000001", Side::Buy, "6")];
    let before = s.account().unwrap();
    let rejection = rejected(s.advance_day(&original, &prices).unwrap());
    assert_eq!(rejection.orders, original);
    assert!(matches!(
        rejection.issues.as_slice(),
        [Issue::InsufficientCash { .. }]
    ));
    let mut pinned = before.clone();
    pinned.snapshot_pinned = true;
    assert_eq!(s.account().unwrap(), pinned);
    let repaired = vec![order("000001", Side::Buy, "2")];
    assert_eq!(
        s.advance_day(&repaired, &market(date(1), &[])),
        Err(SessionError::MarketConflict)
    );
    assert_eq!(s.account().unwrap(), pinned);
    let mut close = advanced(s.advance_day(&repaired, &equal).unwrap());
    assert_eq!(close.executions.len(), 1);
    assert_eq!(close.account.cash, money("6"));
    close.account.cash = Money::ZERO;
    assert_eq!(s.account().unwrap().account.cash, money("6"));
    assert!(!s.account().unwrap().snapshot_pinned);
    // No previous command is replayed on the next, empty day.
    let close = advanced(s.advance_day(&[], &market(date(2), &[])).unwrap());
    assert!(close.executions.is_empty());
    assert_eq!(
        close.account.holdings[0].shares,
        Shares::parse("2").unwrap()
    );
    assert_eq!(close.account.cash, money("6"));
    assert!(close.account.pending_buys.is_empty());
    assert!(close.account.holdings[0].stale);
    assert_eq!(close.account.holdings[0].price_date, date(1));
    let close = advanced(s.advance_day(&[], &market(date(3), &[])).unwrap());
    assert_eq!(
        close.account.holdings[0].shares,
        Shares::parse("2").unwrap()
    );
    assert_eq!(close.account.total_assets, money("10"));
}

#[test]
fn invalid_market_never_pins_and_lifecycle_checks_precede_input() {
    let mut s = Session::default();
    let prices = market(date(1), &[]);
    assert_eq!(
        s.advance_day(&[], &prices),
        Err(SessionError::NotInitialized)
    );
    s.initialize(Config::parse(date(1), date(1), "0").unwrap())
        .unwrap();
    let before = s.account().unwrap();
    assert!(matches!(
        s.advance_day(&[], &market(date(2), &[])),
        Err(SessionError::Market {
            source: MarketError::WrongDate { .. }
        })
    ));
    assert_eq!(s.account().unwrap(), before);
    for year in [0, 10000] {
        assert_eq!(
            MarketSnapshot::new(
                NaiveDate::from_ymd_opt(year, 1, 1).unwrap(),
                BTreeMap::new()
            ),
            Err(MarketError::DateRange)
        );
    }
    assert!(Code::parse("bad").is_err());
    assert!(Nav::parse("0").is_err());
    assert!(Nav::parse("-1").is_err());
    assert!(advanced(s.advance_day(&[], &prices).unwrap()).finished);
    let terminal = s.account().unwrap();
    assert_eq!(
        s.advance_day(&[], &market(date(2), &[])),
        Err(SessionError::Finished)
    );
    assert_eq!(s.account().unwrap(), terminal);
}

#[test]
fn missing_nav_rejects_mixed_batch_and_empty_repair_drops_old_orders() {
    let mut s = session(date(1), date(2), "10");
    let before = s.account().unwrap();
    let prices = market(date(1), &[("000001", Some("1")), ("000002", None)]);
    let orders = vec![
        order("000001", Side::Buy, "1"),
        order("000002", Side::Buy, "1"),
    ];
    let rejection = rejected(s.advance_day(&orders, &prices).unwrap());
    assert_eq!(rejection.orders, orders);
    assert_eq!(rejection.nets.len(), 2);
    assert_eq!(
        rejection.issues,
        vec![Issue::MissingClosingNav {
            code: code("000002")
        }]
    );
    let after = s.account().unwrap();
    assert_eq!(after.account, before.account);
    assert_eq!(after.current_date, before.current_date);
    assert_eq!(after.last_closed_date, None);
    let close = advanced(s.advance_day(&[], &prices).unwrap());
    assert!(close.executions.is_empty());
    assert_eq!(close.account, before.account);
    // Buy and sell with no closing NAV are exempt when their net is zero.
    let close = advanced(
        s.advance_day(
            &[
                order("000002", Side::Buy, "2"),
                order("000002", Side::Sell, "2"),
            ],
            &market(date(2), &[]),
        )
        .unwrap(),
    );
    assert!(close.executions.is_empty());
    assert!(close.skips.is_empty());
}

#[test]
fn three_day_arrivals_rollback_and_no_same_day_sale_proceeds() {
    let mut s = session(date(1), date(3), "10");
    advanced(
        s.advance_day(
            &[order("000001", Side::Buy, "10")],
            &market(date(1), &[("000001", Some("1"))]),
        )
        .unwrap(),
    );
    let opening = s.account().unwrap();
    let prices = market(date(2), &[("000001", Some("2")), ("000002", Some("1"))]);
    let rejection = rejected(
        s.advance_day(
            &[
                order("000001", Side::Sell, "5"),
                order("000002", Side::Buy, "1"),
            ],
            &prices,
        )
        .unwrap(),
    );
    assert_eq!(
        rejection.issues,
        vec![Issue::InsufficientCash {
            required: money("1"),
            available: Money::ZERO
        }]
    );
    assert_eq!(s.account().unwrap().account, opening.account);
    assert_eq!(s.account().unwrap().last_closed_date, Some(date(1)));
    let close = advanced(
        s.advance_day(&[order("000001", Side::Sell, "5")], &prices)
            .unwrap(),
    );
    assert_eq!(
        close.account.holdings[0].shares,
        Shares::parse("5").unwrap()
    );
    assert!(close.account.pending_buys.is_empty());
    assert_eq!(close.account.cash, Money::ZERO);
    assert_eq!(close.account.pending_cash[0].amount, money("10"));
    // Arrival needs no NAV, and rollback also preserves pending cash.
    let rejection = rejected(
        s.advance_day(&[order("000002", Side::Buy, "1")], &market(date(3), &[]))
            .unwrap(),
    );
    assert!(matches!(
        rejection.issues[0],
        Issue::MissingClosingNav { .. }
    ));
    assert_eq!(s.account().unwrap().account, close.account);
    let close = advanced(s.advance_day(&[], &market(date(3), &[])).unwrap());
    assert_eq!(close.account.cash, money("10"));
    assert!(close.account.pending_cash.is_empty());
    assert_eq!(close.account.total_assets, money("20"));
    assert!(close.finished);
}

#[test]
fn pending_cash_is_available_for_next_days_buy() {
    let mut s = session(date(1), date(3), "1");
    for (day, side) in [(1, Side::Buy), (2, Side::Sell), (3, Side::Buy)] {
        let close = advanced(
            s.advance_day(
                &[order("000001", side, "1")],
                &market(date(day), &[("000001", Some("1"))]),
            )
            .unwrap(),
        );
        assert_eq!(close.executions.len(), 1);
        assert_eq!(close.account.total_assets, money("1"));
        assert!(close.account.pending_cash.len() <= 1);
    }
    let end = s.account().unwrap();
    assert_eq!(end.account.pending_buys.len(), 1);
    assert_eq!(end.account.pending_buys[0].due_date, date(4));
    assert!(end.account.pending_cash.is_empty());
    assert!(end.account.holdings.is_empty());
}

#[test]
fn terminal_sale_retains_pending_cash_without_liquidating_remaining_shares() {
    let mut s = session(date(1), date(2), "2");
    advanced(
        s.advance_day(
            &[order("000001", Side::Buy, "2")],
            &market(date(1), &[("000001", Some("1"))]),
        )
        .unwrap(),
    );
    let close = advanced(
        s.advance_day(
            &[order("000001", Side::Sell, "1")],
            &market(date(2), &[("000001", Some("1"))]),
        )
        .unwrap(),
    );
    assert!(close.finished);
    assert_eq!(close.account.cash, Money::ZERO);
    assert_eq!(
        close.account.holdings[0].shares,
        Shares::parse("1").unwrap()
    );
    assert_eq!(close.account.pending_cash[0].amount, money("1"));
    assert_eq!(close.account.pending_cash[0].due_date, date(3));
    assert_eq!(close.account.total_assets, money("2"));
}

#[test]
fn natural_calendar_terminal_pending_and_year_9999() {
    let start = NaiveDate::from_ymd_opt(2024, 2, 28).unwrap();
    let leap = start.succ_opt().unwrap();
    let end = NaiveDate::from_ymd_opt(2024, 3, 1).unwrap();
    let mut s = session(start, end, "1");
    for (day, next) in [(start, leap), (leap, end)] {
        advanced(s.advance_day(&[], &market(day, &[])).unwrap());
        let snapshot = s.account().unwrap();
        assert_eq!(snapshot.current_date, next);
        assert_eq!(snapshot.last_closed_date, Some(day));
        assert_eq!(snapshot.status, Status::Running);
    }
    let close = advanced(
        s.advance_day(
            &[order("000001", Side::Buy, "1")],
            &market(end, &[("000001", Some("1"))]),
        )
        .unwrap(),
    );
    assert!(close.finished);
    assert_eq!(
        close.account.pending_buys[0].due_date,
        end.succ_opt().unwrap()
    );
    let terminal = s.account().unwrap();
    assert_eq!(terminal.current_date, end);
    assert_eq!(terminal.last_closed_date, Some(end));
    assert_eq!(terminal.status, Status::Finished);
    assert!(!terminal.snapshot_pinned);
    assert_eq!(
        s.advance_day(&[], &market(end, &[])),
        Err(SessionError::Finished)
    );
    assert_eq!(s.account().unwrap(), terminal);

    let last = NaiveDate::from_ymd_opt(9999, 12, 31).unwrap();
    let mut s = session(last, last, "1");
    let prices = market(last, &[("000001", Some("1"))]);
    let before = s.account().unwrap();
    let rejection = rejected(
        s.advance_day(&[order("000001", Side::Buy, "1")], &prices)
            .unwrap(),
    );
    assert_eq!(rejection.issues, vec![Issue::DueDateOverflow]);
    assert_eq!(s.account().unwrap().account, before.account);
    assert!(advanced(s.advance_day(&[], &prices).unwrap()).finished);
    assert_eq!(s.account().unwrap().current_date, last);
}
