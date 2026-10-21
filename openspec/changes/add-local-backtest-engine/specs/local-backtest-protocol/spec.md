## ADDED Requirements

The capability name is retained for continuity; its contract is the in-process Python binding, not a wire protocol. There is no binary, CLI, JSON Lines transport, or async runtime. Historical note: the former CLI and process tests were removed; their passing results are not current evidence.

Implementation-stage note: the binding skeleton creates only an empty native session. initialize always raises NotImplementedError; other business methods raise native NotInitializedError. Only outer Python types are checked. No business configuration, account, or report is created. The future business requirements below remain unimplemented; this skeleton is not compliant with every final scenario.

### Requirement: Typed in-process Python entry point
The Python entry point SHALL be a maturin mixed package with a thin typed wrapper and private PyO3 0.30.1 extension `fofoca_engine._native`, targeting CPython 3.12 as the sole supported baseline. BacktestSession construction SHALL own one independent empty Rust Session. The wrapper SHALL own no second business state. Calls SHALL use direct object/exception conversion, not a JSON protocol, child process, HTTP, or async runtime. Module-level NotInitializedError SHALL derive from RuntimeError. The core SHALL expose a controlled synchronous Rust library API without PyO3 or wire/JSON types. Default Rust-only linkage SHALL not require Python; the optional python feature SHALL enable the private binding. There SHALL be no binary target or cli feature. Neither Rust nor Python callers SHALL require database credentials, networking, persistence, or recovery.

For this skeleton, initialize SHALL expose `(start_date: datetime.date, end_date: datetime.date, initial_cash: str)` and raise NotImplementedError without retaining configuration. account, set_orders, advance_day, and report SHALL raise NotInitializedError while uninitialized. set_orders SHALL have a typed list of code/side/decimal-string shares; advance_day SHALL have a typed date/NAV map with decimal-string-or-None values. Only outer argument types SHALL be runtime-checked in this phase; full domain validation/output types remain unimplemented. Float precision SHALL NOT be promised. The package SHALL ship typing metadata. No method SHALL fabricate an account or report success.

#### Scenario: Installed wheel operates without a child process
- **WHEN** the built wheel is installed in an isolated Python 3.12 environment, imported with process creation prohibited, and two sessions are constructed
- **THEN** they own distinct native objects; initialize raises NotImplementedError, account/report/order/day operations raise native NotInitializedError, invalid outer types raise TypeError, and no child process or runtime is required

#### Scenario: Rust-only library remains independent
- **WHEN** a Rust caller builds/tests with default features and separately enables python using CPython 3.12
- **THEN** default builds require no Python, both configurations use the same synchronous session implementation, and neither builds a binary or requires an async runtime

### Requirement: Direct method inputs and lifecycle
The binding SHALL expose initialize, account, set_orders, advance_day, and report as direct methods. initialize SHALL accept datetime.date start/end and decimal-string cash; set_orders SHALL accept a list of `{code, side: "buy" | "sell", shares: decimal_string}`; advance_day SHALL accept `{date: datetime.date, unit_nav: {code: decimal_string | None}}`. Empty order lists and market maps SHALL be valid domain inputs. The decimal/date/code rules SHALL follow local-backtest-session; unknown fields, invalid sides, and numeric share/NAV objects SHALL be rejected without mutation once business validation is implemented. Python dictionaries SHALL supply unique keys directly; no JSON line parsing, request IDs, or response envelopes SHALL be required.

Before successful initialization, business methods SHALL reject with NotInitializedError. After successful initialization, initialize SHALL reject as already_initialized without resetting the account. After finish, set_orders and advance_day SHALL reject as session_finished. Repeating an advance with an old date SHALL not repeat execution. These successful lifecycle paths remain future business work.

#### Scenario: Invalid replacement preserves intentions
- **WHEN** an initialized session receives an order with an extra field, invalid side, non-ASCII code, or numeric shares
- **THEN** the method raises a validation error without replacing previous orders

#### Scenario: Account lifecycle methods
- **WHEN** account is called before initialize, report is called after an unfinished close, or set_orders is called after finish
- **THEN** the calls respectively raise NotInitializedError, return a read-only unfinished report, and reject as session_finished

### Requirement: Account projections and close results
Future account results SHALL expose status, start_date, end_date, current_date, last_closed_date, snapshot_pinned, accepted canonical orders, and the Account projection defined in reporting. Original decimal spelling/order SHALL remain available in submitted-order evidence, not be overwritten by canonical values. A successful advance SHALL distinguish the closed DailyRecord from the next opening or finished terminal account. Snapshot pin status SHALL disclose the retry-cache side effect without changing committed valuation. Future domain inputs/outputs SHALL be strongly typed Rust values; this requirement SHALL NOT introduce JSON or Python objects into the core. Exact Python output types SHALL be introduced with business implementation, not fabricated by the rejecting skeleton.

#### Scenario: Next opening versus closing record
- **WHEN** a nonterminal advance succeeds
- **THEN** its daily record describes the date closed, the account's current_date is the next natural date with arrivals already processed, and closing_account preserves pre-next-opening balances

### Requirement: Untruncated business rejection evidence
Future business errors SHALL preserve attempted inputs, original intentions, derived net operations, provisional skips, and every independently discoverable validation issue with relevant field paths and exact quantities. Validation stages dependent on invalid earlier values SHALL not invent results. Net overflow SHALL identify the affected code without fabricating net shares. Aggregate affordability issues SHALL include available_cash and required_cash; oversells SHALL include confirmed_shares and requested_shares. Diagnostics SHALL NOT be masked or arbitrarily truncated for information policing. Errors SHALL be converted directly to Python exceptions and SHALL NOT require a shared error module, transport envelope, raw JSON line, or failure archive. Core atomicity SHALL follow local-backtest-session.

#### Scenario: Repair a rejected close
- **WHEN** two executable buys exceed aggregate opening cash and one executable sell exceeds confirmed shares
- **THEN** the rejection exposes the original list, derived nets, independent insufficiency issues with exact quantities, provisional skips, and unchanged account/date; replacing the list and retrying the same market can succeed once

### Requirement: Read-only queries and explicit object lifetime
Account and report methods SHALL have no side effects, including no snapshot pin, settlement, date advancement, order clearing, or price-cache update. Object disposal SHALL discard all unexported in-memory state without automatic close, settlement, liquidation, export, or recovery. Constructing a new session SHALL start uninitialized. Python SHALL explicitly save a report if needed.

#### Scenario: Queries preserve a rejected candidate
- **WHEN** account and report are requested repeatedly after a rejected close pins market prices
- **THEN** orders, date, opening balances, committed valuation, pending assets, and snapshot pin remain unchanged and no rejected daily row appears

#### Scenario: Disposal does not drain pending assets
- **WHEN** a session with unsettled trades is disposed and a new session is constructed
- **THEN** no settlement or export occurs and the new session has no restored account
