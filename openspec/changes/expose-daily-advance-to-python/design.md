## Context

`fofoca-engine` owns account state and already exposes a tested Rust `Session::advance_day` operation. The current PyO3 adapter only supports initialization and detached account queries; its trading methods intentionally return `NotImplemented`. The Python facade must become a thin input/output adapter while preserving Rust as the only ledger owner.

The public Python call is intentionally smaller than the Rust market constructor:

```python
session.advance_day(orders, {"000001": "1.234567", "000002": None})
```

The current Rust session date is the only date authority. Python does not submit a duplicate date.

## Goals / Non-Goals

**Goals:**

- Expose the existing Rust daily state transition through the CPython 3.12 PyO3 extension.
- Parse and validate complete order lists and NAV mappings before invoking Rust.
- Return detached, typed Python mappings for successful and business-rejected outcomes.
- Map lifecycle and input failures to explicit Python exceptions without exposing mutable Rust state.
- Test the installed wheel, including a multi-day buy/arrival/sell flow and retry behavior.

**Non-Goals:**

- Changing Rust settlement, arrival, market-pinning, or account semantics.
- Implementing reports, daily records, persistence, networking, or upstream data access.
- Maintaining a Python-side account or order queue.
- Preserving the obsolete `set_orders` or one-argument `advance_day` API.

## Decisions

### Python owns conversion, Rust owns behavior

The native method accepts Python containers, constructs `Code`, `Shares`, `Order`, `Nav`, and `MarketSnapshot`, then calls the existing Rust session. The Python facade remains a thin typed wrapper. A Python ledger was rejected because it would duplicate authoritative state and weaken atomic retry behavior.

### Market input is a NAV mapping

The second argument is `Mapping[str, str | None]`; the native adapter obtains the current date from the Rust session when constructing `MarketSnapshot`. A date-bearing market object was rejected because it duplicates `Session.current_date` and permits conflicting dates.

### Business rejection is a returned mapping

`AdvanceOutcome::Advanced` and `AdvanceOutcome::Rejected` become detached dictionaries with a stable `status` discriminator. Insufficient cash, oversell, and missing closing NAV remain normal strategy-repair results. Uninitialized, finished, wrong input shape, invalid values, and market conflicts raise Python exceptions.

### Conversion errors use existing Python exception categories

Container and field-shape errors use `TypeError`; invalid code, side, date-independent decimal grammar, and invalid NAV values use `ValueError`; numeric overflow uses `OverflowError`; Rust session lifecycle failures use the existing native exceptions or a dedicated market-conflict/runtime mapping. No new dependency or wire protocol is introduced.

### The obsolete order setter is removed

Orders are per-call input to `advance_day`; the native and facade `set_orders` methods are removed. This prevents hidden order state and makes every retry submit a clean complete command list.

## Risks / Trade-offs

- [Risk] PyO3 dictionary/list conversion can accidentally expose references or share mutable nested objects → construct new Python containers for every result and keep Rust-owned values private.
- [Risk] Python callers may rely on the old `set_orders` or market shape → treat the API change as breaking and update the wheel contract in the same change; no compatibility shim should preserve ambiguous semantics.
- [Risk] Rust error variants may not map one-to-one to existing Python exceptions → test each lifecycle and conversion category and use explicit stable messages/categories rather than leaking implementation details.
- [Risk] Installed-wheel tests can accidentally import the source tree → build/install the CPython 3.12 wheel into an isolated environment and assert the package path is under `site-packages`.
