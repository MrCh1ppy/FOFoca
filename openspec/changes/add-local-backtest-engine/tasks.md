## 1. Foundation

- [x] 1.1 Create independent edition-2024 `fofoca-engine/` library and optional thin CLI crate with Tokio current-thread (macros/rt/io-std/io-util), module-local SNAFU errors, serde/JSON, checked-decimal/date dependencies, and Cargo.lock; verify locked check without database/environment requirements or workspace changes. Cargo.lock is present; this task does not claim a git commit.

Phase 1 scope: only crate/module skeleton, strict transport and command shapes, structured rejection, and quit are authorized. Init must not succeed; all state stays null. Tasks 3.x, 5.3, and 6.x may have partial skeleton evidence but remain unchecked until their complete business contracts are implemented. No precision, account, settlement, valuation, or report implementation is included. Later phases need further authorization; this is not a full apply or a claim of final spec compliance.

Historical phase-1 evidence (before the approved binding adjustment): `fofoca-engine/` contained Cargo.toml/Cargo.lock, the narrow CLI library facade, current-thread binary, async generic CLI, protocol parser, uninitialized synchronous session, four documentation-only domain modules, README, and offline tests. Locked check/test/clippy and fmt passed. Eight Rust unit tests and fourteen bounded Python real-process tests covered strict parsing, failures, flush, quit/EOF, and no fabricated business state. That subprocess-only entry-point decision is superseded; its tests are retained under cli. This evidence never completed the business portions of 3.x, 5.3, or 6.x. No production access or git commit was performed.
- [ ] 1.2 Implement strict decimal/date/code types and fixed6/fixed12 serializers, checking parsing, input scales, exact normalization, products, sums, and representation overflow; verify fractional and boundary fixtures without rounding or panic.
- [ ] 1.3 Define session/account/pending/order/market/record types and state ownership without persistence or storage traits; verify invalid initialization, empty holdings, inclusive ranges, and repeat-init rejection.

## 2. Core Daily Account

- [ ] 2.1 Implement atomic whole-list order replacement and checked per-code signed netting with stable original ordering and sorted nets; verify invalid replacement retention and zero-net outcomes.
- [ ] 2.2 Implement current-date unit-NAV validation, normalized snapshot pin/equality/conflict rules, and separate successful-day price cache; verify invalid input never pins and rejected candidates never reprice accounts.
- [ ] 2.3 Implement missing-NAV skips before sufficiency checks, aggregate opening-cash affordability, confirmed-share checks, and collection of independent rejection issues; verify skipped oversells and no sale-proceeds recycling.
- [ ] 2.4 Implement candidate-state buy/sell execution, D+1 pending items, checked next-opening arrivals, and atomic commit; verify rejected closes preserve opening arrivals, dates, orders, and records.
- [ ] 2.5 Implement exact component valuation, price dates/staleness, pending-asset conservation, and valuation_unavailable rejection; verify candidate prices commit only with a successful close.
- [ ] 2.6 Implement successful-close clearing, natural-day advancement, and final-day finish without next opening/drain/liquidation; verify duplicate old dates, unrepresentable due dates, and retained terminal pending assets.

## 3. Optional Secondary JSON Lines Protocol

- [ ] 3.1 Implement sequential UTF-8 JSON Lines input, recursive duplicate-key rejection, strict command schemas, and request_id recovery rules; verify malformed/unknown requests leave the process alive and state unchanged.
- [ ] 3.2 Wire init/account/set_orders/advance_day/report/quit to the core with exact response/state envelopes and lifecycle restrictions; verify successful close data versus next-opening state.
- [ ] 3.3 Implement structured errors with raw line/request, original lists, derived nets, provisional skips, exact quantities, paths, and independent issues without diagnostic masking or a failure archive; verify replacement-and-retry responses.
- [ ] 3.4 Implement stdout single-line response flushing, stderr-only logs, quit acknowledgement, empty EOF and final unterminated-line behavior; verify no automatic settlement/export and fresh uninitialized restarts.

## 4. JSON Reporting

- [ ] 4.1 Implement ReportV1 configuration, assumptions, dates/status, initial/terminal Account schemas, and exact simple profit; verify unfinished and finished exports include no unsupported total-return claims.
- [ ] 4.2 Build successful daily records containing canonical market, original-spelling and canonical orders, nets, executions, skips, closing account, and price/due dates; verify deterministic ordering and no rejected or duplicate daily rows.
- [ ] 4.3 Implement side-effect-free account/report projections from committed state; verify repeated reports and reports across rejected pinned markets preserve identical report content and pending assets.

## 5. Validation

- [ ] 5.1 Add the hand-calculated three-day 100 -> 100 -> 110 -> 110 asset fixture, asserting exact cash, confirmed/pending shares, sale cash, price dates, and every daily total partition.
- [ ] 5.2 Add core regression coverage for weekend missing-NAV settlement, missing/null skips, net zero, multi-fund aggregate overspend, oversell, atomic replacement/retry, final-day pending buys/sales, overflow, and impossible valuation guard behavior.
- [ ] 5.3 Add offline subprocess tests for strict JSON/duplicates/schema/numeric failures, correlated envelopes, fixed market retry, read-only reports, lifecycle errors, stdout purity, quit, EOF, and unterminated final input; run locked cargo check/test with no service credentials.

## 6. Documentation

- [ ] 6.1 Document a complete typed Python strategy example that prepares daily NAV, submits share intentions, repairs a rejection, and explicitly saves ReportV1, plus optional CLI commands, without engine networking or automatic scheduling. Skeleton build/import documentation is not completion of this business example.
- [ ] 6.2 Document decimal bounds/exactness, trusted-author information policy, natural-day zero-fee/no-distribution assumptions, pinned versus committed market behavior, stale prices, terminal pending assets, and loss of unexported state on quit/EOF; cross-check examples against protocol fixtures.

## 7. Approved Binding-Only Skeleton Adjustment

This small stage changes the primary entry point, not accounting scope or final capability completion. Core initialize remains an argument-free rejection until typed domain configuration is authorized; the Python signature reserves dates and decimal-string cash without retaining or validating business configuration. Account/report output annotations are placeholders for rejecting methods, not implemented projections.

- [x] 7.1 Expose controlled synchronous Session/SessionError; isolate default Rust-only builds from optional python/cli adapters, preserve lib+bin and module-local SNAFU, and retain Tokio only at the CLI boundary.
- [x] 7.2 Add stable PyO3 with CPython 3.12 as the sole supported/validated baseline, maturin mixed packaging, private native extension, thin typed wrapper and typing metadata; provide independent empty objects and direct typed exceptions without JSON/subprocess/runtime or duplicate state.
- [x] 7.3 Validate fmt and locked check/test/clippy feature combinations, real installed wheel import/type/exception/rejection behavior with process creation prohibited, and retained CLI regressions; record exact environment/results before marking complete.
- [x] 7.4 Synchronize proposal/design/specs/README with primary PyO3 and optional CLI, document isolated build/install commands and unimplemented scope, and retain all accounting/report tasks unchecked.

Current 3.12 baseline evidence: stable PyO3 0.30.1, maturin 1.15.0, CPython 3.12.13 (`requires-python = ">=3.12,<3.13"`; `.python-version` is a version-manager hint only). A locked/release maturin build produced `fofoca_engine-0.1.0-cp312-cp312-manylinux_2_34_x86_64.whl` in `/tmp/opencode/fofoca-engine-wheels-312`; it was force-reinstalled into the isolated `/tmp/opencode/fofoca-engine-test-312` venv, and `python -I fofoca-engine/tests/wheel_contract.py` passed eight real-native tests with an audit hook prohibiting process creation before import and PATH empty. No mock engine was used. `PYO3_PYTHON=python3.12 cargo test --locked --all-features` passed eight Rust unit tests plus the CLI process test (fourteen Python assertions via `tests/process.rs`, which invokes `python3.12` explicitly); fmt --check, all-features all-targets clippy with `-D warnings`, and OpenSpec strict validation passed. No global environment or cargo config changes were made.

Historical 3.14 evidence (superseded, retained only as history): CPython 3.14.7 previously produced a cp314 manylinux_2_34_x86_64 wheel that installed and passed the same eight wheel tests, but ordinary Python-feature cargo test failed at linking with `unable to find library -lpython3.14`. Because the wheel is version-specific non-abi3, that is neither a current support claim nor a promise that the cp312 wheel imports under 3.14. Free-threaded Python, other operating systems/architectures, full business validation, and business scenarios remain unvalidated/unimplemented. No production access, whole-change apply, git staging/commit, or push occurred.
