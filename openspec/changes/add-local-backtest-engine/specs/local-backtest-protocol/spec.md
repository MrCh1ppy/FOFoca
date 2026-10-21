## ADDED Requirements

Implementation-stage note (not a replacement for final business requirements): the approved phase-1 adjustment makes a PyO3 extension plus thin typed Python wrapper the primary entry point. The independent edition-2024 Rust lib remains synchronous and Python/wire-independent by default; optional cli retains src/bin/fofoca-engine.rs and a Tokio current-thread loop (macros/rt/io-std/io-util). Errors remain module-local SNAFU. Python initialize always raises NotImplementedError; other methods raise native NotInitializedError. No business configuration/state/report is created. In the optional CLI only quit succeeds; init returns not_implemented and other business commands return not_initialized, with state=null. Outer Python types and CLI command shapes are checked, but business date/code/decimal precision, successful initialization, and reports remain deferred. This skeleton is not compliant with every final scenario below.

### Requirement: Primary typed in-process Python entry point
The primary Python entry point SHALL be a maturin mixed package with a thin typed wrapper and private PyO3 extension `fofoca_engine._native`. BacktestSession construction SHALL own one independent empty Rust Session. The wrapper SHALL own no second business state. Calls SHALL use direct object/exception conversion, not a JSON protocol, child process, HTTP, or Tokio runtime. Module-level NotInitializedError SHALL derive from RuntimeError. The core SHALL expose a controlled synchronous Rust library API without PyO3 or wire/JSON types. Default Rust-only linkage SHALL not require Python; Python and CLI adapters SHALL be optional features using the same session implementation. The CLI response/error envelopes below SHALL apply only to the optional CLI, not to native method calls.

For this skeleton, initialize SHALL expose `(start_date: datetime.date, end_date: datetime.date, initial_cash: str)` and raise NotImplementedError without retaining configuration. account, set_orders, advance_day, and report SHALL raise NotInitializedError while uninitialized. set_orders SHALL have a typed list of code/side/decimal-string shares; advance_day SHALL have a typed date/NAV map with decimal-string-or-null values. Only outer argument types SHALL be runtime-checked in this phase; full domain validation/output types remain unimplemented. Float precision SHALL NOT be promised. The package SHALL ship typing metadata. No method SHALL fabricate an account or report success.

#### Scenario: Installed wheel operates without a child process
- **WHEN** the built wheel is installed in an isolated Python 3.12 environment, imported with process creation prohibited, and two sessions are constructed
- **THEN** they own distinct native objects; initialize raises NotImplementedError, account/report/order/day operations raise native NotInitializedError, invalid outer types raise TypeError, and no child process or runtime is required

#### Scenario: Rust-only library remains independent
- **WHEN** a Rust caller builds/tests with default features and separately enables cli
- **THEN** default builds require no Python or Tokio and the optional CLI retains its existing real-process contracts without a second business state

### Requirement: Resident single-session JSON Lines transport
When the optional cli feature is enabled, the CLI SHALL run one in-memory session per process, read UTF-8 JSON Lines sequentially from stdin, and emit exactly one single-line JSON response followed by a flush for each received line. It SHALL write logs only to stderr and never emit banners or logs on stdout. CLI-only builds SHALL require no PyO3 or Python runtime (offline process tests may use Python). Neither adapter SHALL require HTTP, database, network connection, persistence, or recovery. Empty/whitespace-only CLI input lines SHALL be malformed requests, not silently ignored. Malformed JSON, unknown commands, and request validation failures SHALL return structured errors without terminating the loop or mutating session state. Objects SHALL reject duplicate keys at every nesting level before normal deserialization, including envelope and market maps; unknown fields SHALL be rejected rather than silently ignored.

#### Scenario: Malformed line followed by valid request
- **WHEN** a malformed JSON line, a duplicate-key object, an unknown command, and then a valid init line arrive
- **THEN** the process emits four responses, the first three fail without initializing, the fourth succeeds, and stdout contains only JSON Lines

### Requirement: Request envelopes and command schemas
Every request SHALL be exactly `{request_id, command, params}`, with nonempty string request_id, command string, and params object. IDs SHALL correlate responses only, with no persistent idempotency registry or replay behavior. Valid schemas SHALL be:

| Command | Exact params | Success data |
| --- | --- | --- |
| `init` | `{start_date: date, end_date: date, initial_cash: decimal_string}` | `{}` |
| `account` | `{}` | `{}` (account is in state) |
| `set_orders` | `{orders: [{code: code, side: "buy" or "sell", shares: decimal_string}, ...]}` | `{}` |
| `advance_day` | `{market: {date: date, unit_nav: {code: decimal_string or null, ...}}}` | `{day: DailyRecord}` |
| `report` | `{}` | `{report: ReportV1}` |
| `quit` | `{}` | `{}` |

The decimal/date/code contracts SHALL be those in `local-backtest-session`. All fields SHALL be required except arbitrary keys in unit_nav; empty orders and maps SHALL be valid. Before initialization only init and quit SHALL be allowed; other commands SHALL return `not_initialized`. After initialization init SHALL return `already_initialized`; after finished, set_orders/advance_day SHALL return `session_finished` without mutation. The session SHALL accept sequential requests without requiring globally unique IDs, but repeating an advance with an old date SHALL not repeat execution.

Example valid initialization (a single input line):

```json
{"request_id":"i1","command":"init","params":{"start_date":"2026-01-01","end_date":"2026-01-03","initial_cash":"100.000000000000"}}
```

Example order and matching advance (each object is its own line):

```json
{"request_id":"o1","command":"set_orders","params":{"orders":[{"code":"000001","side":"buy","shares":"10.000000"}]}}
{"request_id":"d1","command":"advance_day","params":{"market":{"date":"2026-01-01","unit_nav":{"000001":"2.000000","000002":null}}}}
```

#### Scenario: Exact schema enforcement
- **WHEN** an order contains an extra field, invalid side, non-ASCII code, JSON float shares, or a request omits params
- **THEN** it returns `invalid_request` or the relevant field validation error without replacing the previous orders

#### Scenario: Account lifecycle commands
- **WHEN** account is called before init, report is called after an unfinished close, or set_orders is called after finish
- **THEN** the responses respectively return `not_initialized`, a successful unfinished report, and `session_finished`

### Requirement: Complete response and state envelope
Every response SHALL contain exactly `{request_id, ok, data, error, state}`. On success, ok SHALL be true, data SHALL have the command-specific shape above, and error SHALL be null. On failure, ok SHALL be false, data SHALL be null, and error SHALL follow the error schema below. Invalid JSON/duplicate-key lines SHALL use request_id=null even if a textual ID appears recoverable; otherwise a valid nonempty string request_id SHALL be echoed, or null if absent/invalid. State SHALL be null while uninitialized, otherwise exactly:

```text
State = {
  status: "running" | "finished",
  start_date: date, end_date: date, current_date: date,
  last_closed_date: date | null,
  snapshot_pinned: boolean,
  orders: [OriginalOrder],
  account: Account
}
OriginalOrder = {code: code, side: "buy" | "sell", shares: fixed6}
```

Account SHALL have the `Account` schema from reporting; its valuation_date SHALL be current_date and it SHALL use only committed prices. A successful advance's data.day SHALL describe the closed date while state SHALL describe the next opening or finished terminal account. Snapshot contents SHALL appear in successful daily records; snapshot_pinned SHALL disclose the retry-cache side effect without changing account valuation. Original input spelling and ordering SHALL additionally remain available in `submitted_orders` and rejection raw_request; canonical state.orders SHALL not replace that evidence. Output objects/arrays SHALL use ordinary JSON types; all amounts SHALL remain strings, not JSON numbers.

Example cash-only successful init response:

```json
{"request_id":"i1","ok":true,"data":{},"error":null,"state":{"status":"running","start_date":"2026-01-01","end_date":"2026-01-03","current_date":"2026-01-01","last_closed_date":null,"snapshot_pinned":false,"orders":[],"account":{"valuation_date":"2026-01-01","cash":"100.000000000000","holdings":[],"pending_buys":[],"pending_cash":[],"confirmed_value":"0.000000000000","pending_buy_value":"0.000000000000","pending_cash_value":"0.000000000000","total_assets":"100.000000000000"}}}
```

#### Scenario: Next opening versus closing record
- **WHEN** a nonterminal advance succeeds
- **THEN** data.day.date equals the date closed, state.current_date is the next natural date with arrivals already processed, and data.day.closing_account preserves the pre-next-opening balances

### Requirement: Structured untruncated errors and retry evidence
Errors SHALL contain exactly `{code, message, raw_request, raw_line, submitted_orders, net_operations, skips, issues}`. raw_line SHALL retain the original received line without its line delimiter; raw_request SHALL be the parsed request or null for invalid JSON/duplicates. submitted_orders SHALL be the original currently stored list for advance errors, the attempted replacement for set_orders errors when it is an array, or null otherwise. net_operations/skips SHALL be arrays for an evaluated advance (empty if none), or null if not evaluated. A net sum overflow SHALL retain an entry with net_shares=null for the affected code and an issue, rather than fabricate a quantity. Rejected-close skips SHALL be explicitly provisional. issues SHALL contain every independently discoverable validation failure as `{code, path, message, code_context, details}`, with path a JSON Pointer or empty string for the whole request, code_context a fund code or null, and details an object of relevant exact strings/JSON values. Validation stages dependent on invalid earlier values SHALL not invent results. Messages and evidence SHALL NOT be masked or arbitrarily truncated for information policing. Rejections SHALL not accumulate in session history.

Error codes SHALL include `invalid_json`, `duplicate_key`, `invalid_request`, `unknown_command`, `not_initialized`, `already_initialized`, `session_finished`, `invalid_date`, `date_mismatch`, `invalid_code`, `invalid_decimal`, `invalid_scale`, `numeric_overflow`, `market_conflict`, `batch_rejected`, and `valuation_unavailable`. The top code SHALL identify the first failing stage: line parsing, envelope/command/schema, lifecycle, field validation, snapshot conflict, then candidate execution. Candidate execution failures SHALL use `batch_rejected` with issue codes including `insufficient_cash`, `insufficient_shares`, `numeric_overflow`, `valuation_unavailable`, or `invalid_date`. Aggregate affordability issues SHALL include available_cash and required_cash; oversells SHALL include confirmed_shares and requested_shares. Errors SHALL retain the unchanged opening state, except snapshot_pinned when a valid candidate was accepted. Core atomicity SHALL follow `local-backtest-session`.

#### Scenario: Repair a rejected close
- **WHEN** two executable buys exceed aggregate opening cash and one executable sell exceeds confirmed shares
- **THEN** response includes the full original list, all derived nets, both independent insufficiency issues with exact quantities, provisional skips, and unchanged account/date; a replacement list and same-market advance can succeed once

#### Scenario: Duplicate JSON key identity
- **WHEN** a line contains two request_id fields or a nested repeated fund-code key
- **THEN** it returns `duplicate_key` with request_id=null, raw_request=null, original raw_line, and no state change

### Requirement: Read-only projections and explicit process termination
Account and report commands SHALL have no side effects, including no snapshot pin, settlement, date advancement, order clearing, or price-cache update. A valid quit SHALL respond successfully and then exit with status zero. EOF SHALL exit successfully without additional response, automatic close, settlement, liquidation, or report export. If EOF follows a nonempty unterminated final line, that line SHALL be processed once before exit. Quit and EOF SHALL discard all unexported in-memory state; process restart SHALL begin uninitialized. Diagnostic write/read failures SHALL be process errors rather than pretending a protocol response was delivered; ordinary invalid input SHALL not cause such termination.

#### Scenario: Queries preserve a rejected candidate
- **WHEN** account and report are requested repeatedly after a rejected close pinned market prices
- **THEN** orders, date, opening balances, committed valuation, pending assets, and snapshot pin remain unchanged and no rejected daily row appears

#### Scenario: Quit and EOF do not drain pending assets
- **WHEN** a session has unsettled trades and receives quit, or stdin reaches EOF
- **THEN** it exits successfully without settling or exporting them; quit emits one acknowledgement, empty EOF emits none, and a new process has no restored account
