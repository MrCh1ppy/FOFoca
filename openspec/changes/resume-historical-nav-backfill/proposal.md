## Why

The interrupted manual backfill left roughly 2.27 million NAV rows, followed by a host-wide OOM and a long stall on a small shared host. Resume must avoid unnecessary full-history fetches while relying only on persisted NAV dates, not on a separate success flag that cannot establish coverage.

## What Changes

- **BREAKING** Remove `fund_sync_state` and its success/failure write path; the independent database keeps only `fund` and `fund_nav_daily`. Back up and verify the old table before dropping it; never delete existing fund/NAV data.
- Add optional `backfill --target-date YYYY-MM-DD`: for each currently eligible selected fund, skip before historical fetch if its stored `MAX(nav_date)` is at least the target. With no target, retain ordinary full-history re-fetch, including with `--code`; supplied codes still pass the same eligibility check. The recovery run targets `2026-09-24`. The date is a work threshold, not a proof of contiguous or correct history.
- Accept actual missing historical metric cells (`None`/`NaN`), reject invalid present NAVs, and omit dates with no usable history or dated snapshot NAV; preserve stored non-NULL values and per-fund atomic writes.
- Provide live per-fund progress, truthful final run counts and a list of eligible funds still below the target. Investigate the installed AKShare fetch path before choosing and testing a bounded timeout mechanism; distinguish best-effort transport timeouts from an external hard process bound.
- Update offline/disposable-DB tests and English runbooks; deliver a manually started, single-worker, resource-bounded release with a small-sample gate before a separately authorized full run. No timer, automatic restart after OOM/stall, or Ariadne changes.

## Capabilities

### New Capabilities

- `resumable-historical-nav-backfill`: Target-date NAV-based manual recovery, two-table migration, missing-value behavior, observable fetches and safe manual operation.

### Modified Capabilities

None under `openspec/specs/` (no published specs). This change supersedes the `fund_sync_state` requirements in the earlier unarchived `backfill-off-exchange-fund-nav` draft; its old state semantics are not implementation requirements for the final release.

## Impact

`fofoca-data` CLI, orchestration, history normalization, provider, persistence, bundled schema SQL and runtime `apply_schema`, tests, English README/deployment guidance, and the independent PostgreSQL database. The host shares about 1.6 GiB RAM and 2 GiB swap with Ariadne and PostgreSQL; existing `vm.swappiness=0` is not changed. Eligibility remains the name/daily-feed intersection with dual-open status and known non-money-market type. AKShare supplies complete histories, not date-filtered history.
