"""Backfill orchestration.

Sequential, per-fund loop. Each fund is independent:
  * selection happens once up front (fail-closed);
  * for each eligible fund: fetch -> validate/merge -> commit success in one
    transaction, or record failure in a separate transaction;
  * one fund's failure does not roll back previous funds, and the loop
    continues with subsequent funds;
  * any *unexpected* per-fund error (e.g. ``InvalidOperation`` from a
    malformed Decimal) is treated as a fund-level failure, not a crash —
    the loop continues;
  * a *database* error that prevents recording outcomes aborts the run,
    because we can no longer tell what was persisted.
  * the task exit status is nonzero if selection failed or any eligible fund
    failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from .db import (
    FundWriteOutcome,
    NavWrite,
    record_fund_failure,
    record_fund_success,
)
from .eligibility import FundCandidate, SelectionResult, select_candidates
from .errors import DatabaseError, NavDataError, SelectionError
from .history import FundNavFetch, fetch_fund_nav
from .provider import Provider
from .redact import sanitize_error

from psycopg import Connection


@dataclass
class FundRunResult:
    candidate: FundCandidate
    status: str  # "SUCCESS" | "FAILED"
    partial_coverage: bool = False
    snapshot_fill_count: int = 0
    discrepancy_count: int = 0
    stored_rows: int = 0
    first_data_date: object | None = None
    last_data_date: object | None = None
    error: str | None = None
    discrepancies: list[dict] = field(default_factory=list)


@dataclass
class BackfillReport:
    selection: SelectionResult | None
    selection_error: str | None = None
    fund_results: list[FundRunResult] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.fund_results if r.status == "SUCCESS")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.fund_results if r.status == "FAILED")

    @property
    def ok(self) -> bool:
        return self.selection_error is None and self.failed == 0


def _writes_from_fetch(fetched: FundNavFetch) -> tuple[list[NavWrite], int]:
    """Convert a fetch result into per-date ``NavWrite`` records.

    Historical and snapshot contributions are kept separate so the database
    layer can apply the authoritative-history vs. fill-only-snapshot rule.
    Returns (writes, snapshot_fill_count) where snapshot_fill_count counts
    the (date, metric) slots the snapshot would fill in this run's merged
    view — useful for reporting even if the database already had a value.
    """
    snap_by_date = {p.nav_date: p for p in fetched.snapshot_points}
    all_dates = sorted(
        {p.nav_date for p in fetched.historical_points} | set(snap_by_date)
    )

    hist_by_date = {p.nav_date: p for p in fetched.historical_points}
    writes: list[NavWrite] = []
    snapshot_fill_count = 0
    for d in all_dates:
        hist = hist_by_date.get(d)
        snap = snap_by_date.get(d)
        hist_unit = hist.unit_nav if hist else None
        hist_acc = hist.accumulated_nav if hist else None
        snap_unit = snap.unit_nav if snap else None
        snap_acc = snap.accumulated_nav if snap else None

        # Snapshot is only useful where history is missing.
        useful_snap_unit = snap_unit if hist_unit is None else None
        useful_snap_acc = snap_acc if hist_acc is None else None
        if useful_snap_unit is not None:
            snapshot_fill_count += 1
        if useful_snap_acc is not None:
            snapshot_fill_count += 1

        writes.append(
            NavWrite(
                nav_date=d,
                historical_unit_nav=hist_unit,
                historical_accumulated_nav=hist_acc,
                snapshot_unit_nav=useful_snap_unit,
                snapshot_accumulated_nav=useful_snap_acc,
            )
        )
    return writes, snapshot_fill_count


def _record_failure_safely(
    conn: Connection,
    candidate: FundCandidate,
    error: BaseException | str,
) -> str | None:
    """Best-effort FAILED record; returns extra context if recording itself failed.

    A DB-layer failure here means we can no longer trust outcome persistence,
    so the caller must abort the run.
    """
    try:
        record_fund_failure(conn, candidate, error)
        return None
    except (psycopg.OperationalError, psycopg.InterfaceError) as db_exc:
        raise DatabaseError(
            f"could not record FAILED state for {candidate.code}: "
            f"{sanitize_error(db_exc)}"
        ) from db_exc
    except Exception as db_exc:  # noqa: BLE001
        return f"additionally failed to record FAILED state: {sanitize_error(db_exc)}"


def run_backfill(
    conn: Connection,
    provider: Provider,
    supplied_codes: list[str] | None = None,
) -> BackfillReport:
    """Run the backfill and return a structured report.

    Per-fund failures (fetch, validation, normalization, unexpected
    non-DB exceptions) are captured in the report and the loop continues.
    A selection failure or a fatal database failure is recorded in
    ``selection_error`` / re-raised as ``DatabaseError``.
    """
    try:
        selection = select_candidates(provider, supplied_codes)
    except SelectionError as exc:
        return BackfillReport(selection=None, selection_error=sanitize_error(exc))

    report = BackfillReport(selection=selection)
    snapshot = selection.daily_snapshot
    for candidate in selection.candidates:
        result = FundRunResult(candidate=candidate, status="FAILED")
        try:
            fetched = fetch_fund_nav(provider, candidate.code, snapshot)
            writes, snapshot_fill_count = _writes_from_fetch(fetched)
        except NavDataError as exc:
            result.error = sanitize_error(exc)
            extra = _record_failure_safely(conn, candidate, exc)
            if extra:
                result.error = f"{result.error}; {extra}"
            report.fund_results.append(result)
            continue
        except Exception as exc:  # noqa: BLE001 — unexpected per-fund failure
            # Includes decimal.InvalidOperation, TypeError, KeyError, etc.
            # Treat as a fund-level failure, do not crash the run.
            result.error = f"unexpected: {sanitize_error(exc)}"
            extra = _record_failure_safely(conn, candidate, exc)
            if extra:
                result.error = f"{result.error}; {extra}"
            report.fund_results.append(result)
            continue

        try:
            outcome: FundWriteOutcome = record_fund_success(conn, candidate, writes)
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            # Database is down / connection broken — abort the whole run.
            raise DatabaseError(
                f"database write failed for {candidate.code}: {sanitize_error(exc)}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            # Other DB errors (constraint violations, etc.) are fund-level.
            result.error = f"database write failed: {sanitize_error(exc)}"
            extra = _record_failure_safely(conn, candidate, exc)
            if extra:
                result.error = f"{result.error}; {extra}"
            report.fund_results.append(result)
            continue

        result.status = "SUCCESS"
        result.partial_coverage = fetched.partial_coverage
        result.snapshot_fill_count = snapshot_fill_count
        result.discrepancy_count = len(fetched.discrepancies)
        result.discrepancies = [
            {
                "nav_date": d.nav_date.isoformat(),
                "metric": d.metric,
                "historical": str(d.historical),
                "snapshot": str(d.snapshot),
            }
            for d in fetched.discrepancies
        ]
        result.stored_rows = outcome.stored_rows
        result.first_data_date = outcome.first_data_date
        result.last_data_date = outcome.last_data_date
        report.fund_results.append(result)
    return report


__all__ = ["FundRunResult", "BackfillReport", "run_backfill"]
