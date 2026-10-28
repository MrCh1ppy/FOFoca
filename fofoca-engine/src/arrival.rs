//! Checked arrivals on a disposable opening account; never touches Session state.
use chrono::NaiveDate;

use crate::{
    decimal::DecimalError,
    model::{Account, Holding},
};

pub(crate) fn arrive(account: &mut Account, date: NaiveDate) -> Result<(), DecimalError> {
    let mut future_buys = Vec::new();
    for pending in std::mem::take(&mut account.pending_buys) {
        if pending.due_date > date {
            future_buys.push(pending);
        } else if let Some(holding) = account.holdings.iter_mut().find(|h| h.code == pending.code) {
            holding.shares = holding.shares.checked_add(pending.shares)?;
            // Use the latest committed price; today's revaluation happens in settle.
            if pending.price_date > holding.price_date {
                holding.unit_nav = pending.unit_nav;
                holding.price_date = pending.price_date;
                holding.stale = pending.stale;
            }
            holding.value = holding.shares.checked_value(holding.unit_nav)?;
        } else {
            account.holdings.push(Holding {
                code: pending.code,
                shares: pending.shares,
                unit_nav: pending.unit_nav,
                price_date: pending.price_date,
                stale: pending.stale,
                value: pending.value,
            });
        }
    }
    account.pending_buys = future_buys;
    let mut future_cash = Vec::new();
    for pending in std::mem::take(&mut account.pending_cash) {
        if pending.due_date > date {
            future_cash.push(pending);
        } else {
            account.cash = account.cash.checked_add(pending.amount)?;
        }
    }
    account.pending_cash = future_cash;
    Ok(())
}
