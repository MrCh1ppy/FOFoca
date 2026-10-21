## ADDED Requirements

Adapter-stage note: reports remain unimplemented and the Python binding rejects report calls while uninitialized; no empty successful report is fabricated. JSON below is the future explicit report export contract, not an in-process call protocol or core ledger representation. Python binding calls use direct objects/exceptions, and Rust callers can link the library directly. Report generation must project the single Rust-owned state; object disposal never exports automatically. There is no binary, CLI, or async runtime.

### Requirement: Versioned JSON report and assumptions
The report method SHALL return a JSON-exportable object with exactly the following version-1 fields, usable independently of the session. It SHALL be available during running and finished initialized sessions and SHALL be a read-only projection. No HTML renderer, web server, database, or additional data fetch SHALL be required. Decimal/date/code fields SHALL follow the session contract; arrays keyed by code SHALL be ascending by code, daily records ascending by date, and source orders SHALL retain submitted order. The report SHALL contain no advanced performance metrics beyond the exact profit defined here.

```text
ReportV1 = {
  report_version: 1,
  config: {start_date: date, end_date: date, initial_cash: fixed12},
  assumptions: {
    calendar: "natural_days", execution_price: "same_day_unit_nav",
    settlement: "D+1_natural_day", fees: "zero",
    distributions: "not_modeled", terminal_policy: "retain_pending",
    strategy_and_data_owner: "python", information_policy: "trusted_author",
    numeric_model: "checked_decimal_96bit", shares_nav_scale: 6, money_scale: 12,
    return_caveat: "Unit-NAV model; not a real-world total-return guarantee."
  },
  status: "running" | "finished",
  current_date: date, last_closed_date: date | null,
  initial_account: Account,
  days: [DailyRecord],
  terminal_account: Account,
  profit: fixed12
}
```

Here `fixed6` and `fixed12` SHALL mean decimal strings with exactly six and twelve fractional digits; nonnegative fields SHALL omit signs and profit/net_shares SHALL allow minus. JSON integers SHALL be used only for version/scale/index fields, never financial quantities. initial_account SHALL be the cash-only start-date account. terminal_account SHALL mean the current read-only account even for an unfinished report. Profit SHALL equal terminal total_assets minus initial_cash, without annualization or total-return claims.

#### Scenario: Unfinished and finished exports
- **WHEN** report is requested before any close, after some closes, and after the final close
- **THEN** each export is version 1 with matching status/dates, the initial export has no days and profit zero, and the final export preserves any terminal pending assets

### Requirement: Account component schema
Account SHALL contain exactly the fields below. Each share-bearing component SHALL include its actual valuation unit NAV/date and stale flag relative to valuation_date. Pending cash SHALL be fixed cash claims, not shares. Empty/zero confirmed positions SHALL be omitted. Pending items SHALL be listed by due_date, execution_date, then code; no pending item SHALL be dropped before arrival or terminal export.

```text
Account = {
  valuation_date: date,
  cash: fixed12,
  holdings: [{code, shares: fixed6, unit_nav: fixed6, price_date: date,
              stale: boolean, value: fixed12}],
  pending_buys: [{code, shares: fixed6, execution_date: date, due_date: date,
                  unit_nav: fixed6, price_date: date, stale: boolean, value: fixed12}],
  pending_cash: [{code, amount: fixed12, execution_date: date, due_date: date}],
  confirmed_value: fixed12,
  pending_buy_value: fixed12,
  pending_cash_value: fixed12,
  total_assets: fixed12
}
```

The engine SHALL expose sums matching these components and total_assets SHALL equal their sum plus cash. On arrival it SHALL move a pending buy to confirmed shares or pending cash to available cash exactly once, removing the pending item. Account/report SHALL use only successful-day committed prices; no rejected candidate price SHALL appear. A newly opened date without today's successful close SHALL identify previous-day prices as stale, including when its snapshot is pinned. Closing records SHALL instead use the close date as valuation_date.

#### Scenario: Pending asset conservation in report
- **WHEN** a day closes with cash 80 and 10 pending shares bought at 2, and the next opening confirms them without a new price
- **THEN** the closing record has pending_buy_value 20 and total_assets 100, the opening account has confirmed_value 20 and total_assets 100, and both disclose the price date with appropriate stale flags

#### Scenario: Terminal sale proceeds
- **WHEN** a sale on end_date leaves pending cash due the next natural day
- **THEN** terminal_account contains that cash claim with its execution/due dates and counts it once without including the sold shares or automatically making cash available

### Requirement: Successful daily evidence only
Each successful close SHALL append exactly one DailyRecord with the following exact fields. It SHALL preserve the accepted original intentions, normalized nets, executions, skips, and the day's effective market. Rejected attempts SHALL appear only in call exceptions, not as daily records or a stored failure archive. A retry SHALL never duplicate a successful day. Original submitted_orders SHALL retain the caller's decimal spelling and array ordering, while original_orders SHALL use canonical fixed6 values. They SHALL represent the most recently accepted replacement, not discarded earlier attempts.

```text
DailyRecord = {
  date: date,
  market: {date: date, unit_nav: {code: fixed6, ...}},
  submitted_orders: [{code, side: "buy" | "sell", shares: original_decimal_string}],
  original_orders: [OriginalOrder],
  net_operations: [{code, net_shares: signed_fixed6}],
  executions: [{code, side: "buy" | "sell", shares: fixed6,
                unit_nav: fixed6, cash_amount: fixed12, due_date: date}],
  skips: [{code, net_shares: signed_fixed6,
           reason: "missing_nav" | "no_trade", provisional: false}],
  closing_account: Account
}
OriginalOrder = {code: code, side: "buy" | "sell", shares: fixed6}
```

Only non-null NAV entries SHALL appear in the canonical effective market; omitted/null values are equivalent and remain unavailable. Every original code SHALL have one net entry, and each net SHALL have exactly one execution or skip. Executions, nets, and skips SHALL be sorted by code. Zero nets SHALL be `no_trade`; nonzero missing prices SHALL be `missing_nav`. Provisional rejection skips SHALL use the same skip shape with provisional=true and SHALL never be inserted into a report. Fees SHALL not be silently deducted. The report SHALL not require the complete strategy source or a strategy identifier.

#### Scenario: Failed attempt and replacement evidence
- **WHEN** an advance rejects, Python replaces its orders, and the same day later closes successfully
- **THEN** the report contains one daily row with only the final accepted list, its nets and actual outcomes, no rejected candidate valuation, and no failed-attempt archive

#### Scenario: Missing and zero-net outcomes are distinguishable
- **WHEN** one fund has a nonzero net with missing NAV and another has a zero net
- **THEN** the successful daily record contains distinct `missing_nav` and `no_trade` skips, no executions for either fund, and the correct unchanged economic assets

### Requirement: Deterministic read-only report export
Repeated report calls with no intervening successful mutation SHALL return equal JSON data independent of object key ordering. Export SHALL not settle, clear orders, advance time, pin market, or recalculate from rejected prices. JSON report content SHALL be sufficient to verify daily cash/share settlement and total-asset partitions without rerunning Python or contacting a data source. A consumer SHALL be able to save the returned object directly; automatic file writing SHALL not be part of the engine contract.

#### Scenario: Repeated export across a failed close
- **WHEN** reports are exported before and after a rejected batch that only pins a market candidate
- **THEN** reports are identical, including dates, daily rows, committed price dates, terminal asset totals, and profit
