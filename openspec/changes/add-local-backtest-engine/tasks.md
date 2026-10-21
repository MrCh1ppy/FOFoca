## 1. Foundation

- [x] 1.1 Create independent edition-2024 `fofoca-engine/` library with optional PyO3 binding, module-local SNAFU errors, reserved checked-decimal/date dependencies, and Cargo.lock; verify locked check without database/environment requirements or workspace changes. No binary or async runtime is required.

Phase 1 delivered only the library/binding skeleton. The subsequently approved initialization stage (section 8) adds exact nonnegative decimals, minimal account components, validated configuration, cash-only initialization, and detached account snapshots. Orders, day advancement, settlement, priced valuation, records, and reports remain unimplemented. Tasks 1.2/1.3, 3.x, 5.3, and 6.x have partial evidence but stay unchecked until their complete contracts are implemented. In particular signed net/profit arithmetic/output remains outside this stage, and 1.3 still lacks order/market/record types. Later work requires further authorization; this is not a full apply or a claim of final spec compliance.

Historical note: the former CLI/protocol, binary, and process tests were removed. Their earlier passing results do not describe current validation or complete any business task.
- [ ] 1.2 Implement strict decimal/date/code types and fixed6/fixed12 serializers, checking parsing, input scales, exact normalization, products, sums, and representation overflow; verify fractional and boundary fixtures without rounding or panic.
- [ ] 1.3 Define session/account/pending/order/market/record types and state ownership without persistence or storage traits; verify invalid initialization, empty holdings, inclusive ranges, and repeat-init rejection.

## 2. Core Daily Account

- [ ] 2.1 Implement atomic whole-list order replacement and checked per-code signed netting with stable original ordering and sorted nets; verify invalid replacement retention and zero-net outcomes.
- [ ] 2.2 Implement current-date unit-NAV validation, normalized snapshot pin/equality/conflict rules, and separate successful-day price cache; verify invalid input never pins and rejected candidates never reprice accounts.
- [ ] 2.3 Implement missing-NAV skips before sufficiency checks, aggregate opening-cash affordability, confirmed-share checks, and collection of independent rejection issues; verify skipped oversells and no sale-proceeds recycling.
- [ ] 2.4 Implement candidate-state buy/sell execution, D+1 pending items, checked next-opening arrivals, and atomic commit; verify rejected closes preserve opening arrivals, dates, orders, and records.
- [ ] 2.5 Implement exact component valuation, price dates/staleness, pending-asset conservation, and valuation_unavailable rejection; verify candidate prices commit only with a successful close.
- [ ] 2.6 Implement successful-close clearing, natural-day advancement, and final-day finish without next opening/drain/liquidation; verify duplicate old dates, unrepresentable due dates, and retained terminal pending assets.

## 3. Python Binding Business Contract

- [ ] 3.1 Implement full nested order/market validation and strict business date/code/decimal semantics for direct Python inputs; verify invalid inputs leave the account and accepted orders unchanged.
- [ ] 3.2 Wire initialize/account/set_orders/advance_day/report to typed core domain inputs/outputs with lifecycle restrictions; verify successful close data versus next-opening state without JSON envelopes or duplicate Python state.
- [ ] 3.3 Implement module-local business errors converted to exceptions with original inputs/lists, derived nets, provisional skips, exact quantities, paths, and independent issues without diagnostic masking or a failure archive; verify replacement-and-retry evidence.
- [ ] 3.4 Verify object disposal does not automatically settle/export or recover an account and newly constructed sessions are uninitialized, including with terminal pending assets.

## 4. JSON Reporting

- [ ] 4.1 Implement ReportV1 configuration, assumptions, dates/status, initial/terminal Account schemas, and exact simple profit; verify unfinished and finished exports include no unsupported total-return claims.
- [ ] 4.2 Build successful daily records containing canonical market, original-spelling and canonical orders, nets, executions, skips, closing account, and price/due dates; verify deterministic ordering and no rejected or duplicate daily rows.
- [ ] 4.3 Implement side-effect-free account/report projections from committed state; verify repeated reports and reports across rejected pinned markets preserve identical report content and pending assets.

## 5. Validation

- [ ] 5.1 Add the hand-calculated three-day 100 -> 100 -> 110 -> 110 asset fixture, asserting exact cash, confirmed/pending shares, sale cash, price dates, and every daily total partition.
- [ ] 5.2 Add core regression coverage for weekend missing-NAV settlement, missing/null skips, net zero, multi-fund aggregate overspend, oversell, atomic replacement/retry, final-day pending buys/sales, overflow, and impossible valuation guard behavior.
- [ ] 5.3 Add real installed-wheel business tests for nested type/schema/numeric failures, fixed market retry, read-only reports, lifecycle errors, and disposal without settlement/export; prohibit child processes and run locked cargo check/test with no service credentials.

## 6. Documentation

- [ ] 6.1 Document a complete typed Python strategy example that prepares daily NAV, submits share intentions, repairs a rejection, and explicitly saves ReportV1 without engine networking or automatic scheduling. Skeleton build/import documentation is not completion of this business example.
- [ ] 6.2 Document decimal bounds/exactness, trusted-author information policy, natural-day zero-fee/no-distribution assumptions, pinned versus committed market behavior, stale prices, terminal pending assets, and loss of unexported state on object disposal; cross-check examples against binding fixtures.

## 7. Approved Binding-Only Skeleton Adjustment

Historical phase-1 scope: this stage changed the primary entry point, not accounting scope or final capability completion. At that point core initialize was an argument-free rejection and Python date/cash signatures retained no configuration. Section 8 supersedes only initialization/account behavior; this section is historical evidence, not the current API contract.

- [x] 7.1 Expose controlled synchronous Session/SessionError; isolate default Rust-only builds from the optional python binding and preserve module-local SNAFU, with no binary or async runtime.
- [x] 7.2 Add stable PyO3 with CPython 3.12 as the sole supported/validated baseline, maturin mixed packaging, private native extension, thin typed wrapper and typing metadata; provide independent empty objects and direct typed exceptions without JSON/subprocess/runtime or duplicate state.
- [x] 7.3 Validate fmt and default/all-features locked check/test/clippy, and real installed wheel import/type/exception/rejection behavior with process creation prohibited; record exact environment/results before marking complete.
- [x] 7.4 Synchronize proposal/design/specs/README with library plus PyO3 only, document isolated build/install commands and unimplemented scope, and retain all accounting/report tasks unchecked.

The following post-removal results describe the earlier skeleton only; current initialization-stage evidence is recorded in section 8.

Current post-removal evidence: CPython 3.12.13, PyO3 0.30.1, maturin 1.15.0. `cargo fmt --check` and locked check/test/clippy passed for default and all features; all Python-feature commands explicitly used `PYO3_PYTHON=python3.12`, and clippy used `--all-targets -- -D warnings`. Each cargo test configuration ran one Rust session unit test and zero doc tests; no process tests remain. A fresh locked/release maturin build in `/tmp/opencode/fofoca-engine-build-312` produced `fofoca_engine-0.1.0-cp312-cp312-manylinux_2_34_x86_64.whl` in `/tmp/opencode/fofoca-engine-wheels-312`, which was force-reinstalled into `/tmp/opencode/fofoca-engine-test-312`. `python -I fofoca-engine/tests/wheel_contract.py` passed all eight real-native tests, with process creation prohibited before import and PATH empty. No mocks were used. OpenSpec strict validation and `git diff --check` passed. Cargo emitted only the pre-existing user-config deprecation warning; no global config was changed.

The resolved all-features build tree contains chrono 0.4.45, rust_decimal 1.43.0, snafu 0.8.9, and optional PyO3 0.30.1 as direct dependencies; no Tokio or serde/serde_json is built. Cargo.lock still records serde/serde_json through rust_decimal's optional dependency resolution, not a retained CLI or enabled serialization feature. Chrono's serde feature and the crate's direct serde/serde_json dependencies are removed. Domain placeholder modules remain unchanged.

Historical note: a cp314 wheel previously passed eight wheel tests, but ordinary cargo tests failed to link libpython3.14. This is not a support or cross-version import claim. Free-threaded Python, other operating systems/architectures, full business validation, and business scenarios remain unvalidated/unimplemented. This removal stage does not access production, apply the whole change, stage/commit, or push.

## 8. Approved Exact-Decimal and Initialization Stage

This stage is authorized independently of the remaining business tasks. No task in sections 2–6 is completed by a cash-only snapshot.

- [x] 8.1 Implement strict ASCII Money/Shares/Nav parsing, supplied-digit scale limits, positive external shares/NAV versus zero internal shares/cash, and fixed6/fixed12 unsigned-zero output. Bound fixed-scale coefficients before Decimal construction; use exact integer checks for nonnegative sums/subtraction and shares*Nav products, including overflow rather than rounding.
- [x] 8.2 Define six-digit Code and owned Account/Holding/PendingBuy/PendingCash data with actual required component fields, without orders, markets, records, transaction mutation, or fake history.
- [x] 8.3 Add private validated Config with typed constructor/parse helper and getters; initialize a private Session cash-only account once, validate inclusive year 1..9999 ranges, and return detached owned Snapshot data. Do not pre-reject year 9999 for a future D+1 date, pin prices, or settle.
- [x] 8.4 Wire Python date/string initialization and exact Snapshot/Account TypedDict output with native error variants explicitly mapped to TypeError/ValueError/OverflowError, NotInitializedError, and new native AlreadyInitializedError. Preserve method names; initialized order/day/report calls still raise NotImplementedError. Keep only a native handle in the wrapper and update exports/stub/py.typed packaging.
- [x] 8.5 Validate default and Python-feature Rust builds/tests/clippy/fmt, and rebuild/force-reinstall a real release cp312 wheel in the isolated build/test environments. Test strict numeric/date failures, recoverable initialization, repeat rejection, exact shapes, and independent nested copies, retaining the pre-import no-child-process audit hook and empty PATH.
- [x] 8.6 Synchronize README/proposal/design/capability specs and stage tasks; validate OpenSpec strictly and check the diff. Do not apply the full change, touch reference directories/other applications/production/global environment, or commit/push.

Initialization-stage evidence: CPython 3.12.13, PyO3 0.30.1, maturin 1.15.0. `cargo fmt --check` and locked default/all-features check/test/clippy passed; each Rust configuration ran **11 unit tests and zero doc tests**, and clippy used `--all-targets -- -D warnings`. All Python-feature commands explicitly set `PYO3_PYTHON=python3.12`. A fresh locked release wheel build in the verified `/tmp/opencode/fofoca-engine-build-312` produced `fofoca_engine-0.1.0-cp312-cp312-manylinux_2_34_x86_64.whl` in `/tmp/opencode/fofoca-engine-wheels-312`; it was force-reinstalled into `/tmp/opencode/fofoca-engine-test-312`. `python -I fofoca-engine/tests/wheel_contract.py` passed **16 real-native tests**, with the existing audit hook installed before package import and PATH empty. No production/market data or mocks were needed. OpenSpec strict validation and `git diff --check` passed. The only Cargo warning was the pre-existing user-config deprecation; no global configuration was changed.

Remaining scope: 1.2 stays partial because signed net/profit operations/serialization are not implemented; 1.3 stays partial because order/market/record domain types are absent. All 2.x/3.x/4.x/5.x/6.x remain unchecked. No set_orders/advance_day/report business, next-day settlement, nonempty valuation, records, automatic exports, concurrency wrapper, network, persistence, CLI, Tokio, or protocol.rs was added. No full phase/change completion is claimed.

## 9. Approved Stateless Single-Day Settlement Stage

This stage is a Rust-core calculation only. It does not wire `set_orders`/`advance_day`, does not commit a candidate to `Session`, and does not add records, price pinning, reports, Python binding behavior, networking, or persistence. Tasks 2.1–2.5 remain unchecked because their complete contracts include replacement, pinning, commit/arrival, records, and binding integration.

- [x] 9.1 Add typed `Order`/`Side` construction with positive order shares and a module-local SNAFU `OrderError`; retain public account component fields and the checked Money/Shares/Nav APIs without f64 or rounding.
- [x] 9.2 Implement stateless `settlement::settle(opening, current_date, unit_nav, orders)` returning a structured candidate or rejection: original-order-preserving checked buy-minus-sell nets sorted by code, zero-net `no_trade`, missing-NAV skips before oversell/cash checks, opening-confirmed oversell checks, one aggregate opening-cash affordability check without sale recycling, collected independent issues, and no opening-account mutation.
- [x] 9.3 Build successful candidates with immediate cash/share changes, D+1 pending buys/sales, business-year D+1 rejection, exact product usage, candidate-or-stored NAV revaluation with stale flags, and a checked total partition. The missing-price `valuation_unavailable` guard is not reachable because stored component prices are non-optional, so it is not claimed.
- [x] 9.4 Add Rust unit coverage for buy/sell netting, zero net without NAV, missing-NAV sell/buy bypassing oversell/cash checks, aggregate affordability without sale proceeds, atomic multi-issue rejection, fractional exact product, due dates, year-9999 due-date rejection, and stale/current revaluation; validate fmt, locked default/all-feature tests and clippy, OpenSpec strict, and the diff.

Scope boundary: no `Session` commit, price pin/cache, order replacement, next-opening arrival, daily record, report, PyO3 business method, network, or persistence was added. Evidence: 21 Rust unit tests and zero doc tests passed in both default and all-feature (Python) configurations on CPython 3.12; fmt and clippy (`--all-targets -- -D warnings`) passed; OpenSpec strict validation and `git diff --check` passed.
