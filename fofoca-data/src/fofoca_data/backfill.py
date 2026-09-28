"""Backfill orchestration.

Sequential, per-fund loop (single worker, no threads):

  * selection happens once up front (fail-closed);
  * in ``--target-date`` mode each selected fund's stored ``MAX(nav_date)``
    is read in a short read-only transaction that ends **before** any network
    fetch; funds already at/above the target are reported ``SKIPPED`` with no
    writes and no fetches;
  * for each attempted fund: fetch -> validate/merge -> commit metadata+NAV
    in one transaction. Failures roll back that fund's attempt only;
    previously committed funds stay committed; no failure state is written
    anywhere and no failure-only fund row is inserted;
  * any *unexpected* per-fund error (e.g. ``InvalidOperation`` from a
    malformed Decimal) is treated as a fund-level failure, not a crash —
    the loop continues;
  * a *database* error on the write path aborts the run, because we can no
    longer tell what was persisted;
  * live, flushed, sanitized progress lines are emitted per fund and per
    indicator stage, and a truthful final report distinguishes
    selected/attempted/skipped/succeeded/failed plus, in target mode, the
    eligible codes still below the target after the run.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import date
from typing import TextIO

import psycopg
from psycopg import Connection

from .db import (
    FundWriteOutcome,
    NavWrite,
    read_stored_max_nav_dates,
    record_fund_success,
)
from .eligibility import FundCandidate, SelectionResult, select_candidates
from .errors import DatabaseError, NavDataError, SelectionError
from .history import FundNavFetch, fetch_fund_nav
from .provider import Provider
from .redact import sanitize_error


# ---------------------------------------------------------------------------
# Live progress
# ---------------------------------------------------------------------------


class ProgressReporter:
    """Flushed, sanitized, single-line progress events on a stream (stderr).

    Fund codes are six-digit public identifiers (not credentials), but every
    free-form detail string still passes through :func:`sanitize_error` so a
    leaked DSN/password can never reach the log.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stderr

    def emit(self, event: str, code: str, detail: str = "") -> None:
        line = f"[fofoca] {event} code={code}"
        if detail:
            line += f" {sanitize_error(detail)}"
        print(line, file=self._stream, flush=True)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"

ERROR_CLASS_TIMEOUT = "timeout"
ERROR_CLASS_FETCH = "fetch"
ERROR_CLASS_VALIDATION = "validation"
ERROR_CLASS_DB = "db"
ERROR_CLASS_UNEXPECTED = "unexpected"


def _classify_error(exc: BaseException, text: str) -> str:
    """Best-effort failure classification for the report (no DB state)."""
    name = type(exc).__name__.lower()
    if "timeout" in name or "timed out" in text.lower():
        return ERROR_CLASS_TIMEOUT
    if isinstance(exc, NavDataError):
        if "request failed" in text.lower() or "timeout" in text.lower():
            return ERROR_CLASS_FETCH
        return ERROR_CLASS_VALIDATION
    if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
        return ERROR_CLASS_DB
    return ERROR_CLASS_UNEXPECTED


@dataclass
class FundRunResult:
    candidate: FundCandidate
    status: str  # "SUCCESS" | "FAILED" | "SKIPPED"
    partial_coverage: bool = False
    snapshot_fill_count: int = 0
    discrepancy_count: int = 0
    stored_rows: int = 0
    first_data_date: date | None = None
    last_data_date: date | None = None
    stored_max_nav_date: date | None = None
    error_class: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0
    discrepancies: list[dict] = field(default_factory=list)


@dataclass
class BackfillReport:
    selection: SelectionResult | None
    target_date: date | None = None
    selection_error: str | None = None
    reconciliation_error: str | None = None
    fund_results: list[FundRunResult] = field(default_factory=list)
    below_target_codes: list[str] | None = None

    @property
    def selected(self) -> int:
        return len(self.fund_results)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.fund_results if r.status == STATUS_SKIPPED)

    @property
    def attempted(self) -> int:
        return sum(
            1
            for r in self.fund_results
            if r.status in (STATUS_SUCCESS, STATUS_FAILED)
        )

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.fund_results if r.status == STATUS_SUCCESS)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.fund_results if r.status == STATUS_FAILED)

    @property
    def ok(self) -> bool:
        """True only when the run may exit zero.

        Requirements: selection succeeded, no attempted fund failed, and — in
        target mode — reconciliation ran cleanly and no eligible selected
        code remains below the target. Skipped funds are not fresh successes
        and never mask a shortfall elsewhere.
        """
        if self.selection_error is not None or self.reconciliation_error is not None:
            return False
        if self.failed != 0:
            return False
        if self.target_date is not None:
            if self.below_target_codes is None:
                return False
            if self.below_target_codes:
                return False
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _writes_from_fetch(fetched: FundNavFetch) -> tuple[list[NavWrite], int]:
    """Convert a fetch result into per-date ``NavWrite`` records.

    Historical and snapshot contributions are kept separate so the database
    layer can apply the authoritative-history vs. fill-only-snapshot rule.
    Returns (writes, snapshot_fill_count) where snapshot_fill_count counts
    the (date, metric) slots the snapshot would fill in this run's merged
    view — useful for reporting even if the database already had a value.
    Dates with no usable historical metric and no valid dated snapshot fill
    are omitted entirely (no all-NULL row is ever produced).
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

        write = NavWrite(
            nav_date=d,
            historical_unit_nav=hist_unit,
            historical_accumulated_nav=hist_acc,
            snapshot_unit_nav=useful_snap_unit,
            snapshot_accumulated_nav=useful_snap_acc,
        )
        if not write.has_any_value:
            # No usable historical or valid dated snapshot metric on this
            # date: omit the date, never insert an all-NULL row.
            continue
        writes.append(write)
    return writes, snapshot_fill_count


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_backfill(
    conn: Connection,
    provider: Provider,
    supplied_codes: list[str] | None = None,
    *,
    target_date: date | None = None,
    progress: ProgressReporter | None = None,
) -> BackfillReport:
    """Run the backfill and return a structured report.

    Per-fund failures (fetch, validation, normalization, unexpected
    non-DB exceptions) are captured in the report and the loop continues.
    A selection failure is recorded in ``selection_error``; a fatal database
    failure aborts by raising ``DatabaseError``.

    With ``target_date`` set, each selected fund whose stored
    ``MAX(nav_date)`` is at least the target is reported ``SKIPPED`` before
    either history request, with no writes. A missing fund, no NAV rows, or
    a maximum below the target triggers both complete history fetches. The
    coverage lookup runs in its own short read-only transaction, which is
    fully committed before any network I/O begins.
    """
    progress = progress or ProgressReporter()
    try:
        selection = select_candidates(provider, supplied_codes)
    except SelectionError as exc:
        return BackfillReport(
            selection=None,
            target_date=target_date,
            selection_error=sanitize_error(exc),
        )

    report = BackfillReport(selection=selection, target_date=target_date)

    # Target-mode coverage lookup: one short read-only transaction, fully
    # committed before ANY history fetch or fund write. A lookup failure
    # raises DatabaseError and aborts the run — it is never read as "no
    # stored coverage".
    max_dates: dict[str, date | None] = {}
    if target_date is not None:
        max_dates = read_stored_max_nav_dates(
            conn, [c.code for c in selection.candidates]
        )

    snapshot = selection.daily_snapshot
    total = len(selection.candidates)
    for index, candidate in enumerate(selection.candidates, start=1):
        code = candidate.code
        started = time.monotonic()
        result = FundRunResult(candidate=candidate, status=STATUS_FAILED)

        if target_date is not None:
            stored_max = max_dates.get(code)
            result.stored_max_nav_date = stored_max
            if stored_max is not None and stored_max >= target_date:
                result.status = STATUS_SKIPPED
                result.elapsed_seconds = time.monotonic() - started
                progress.emit(
                    "SKIPPED",
                    code,
                    f"[{index}/{total}] stored_max={stored_max.isoformat()} "
                    f">= target={target_date.isoformat()} "
                    f"({result.elapsed_seconds:.1f}s)",
                )
                report.fund_results.append(result)
                continue

        progress.emit("START", code, f"[{index}/{total}] target={target_date}")
        try:
            fetched = fetch_fund_nav(
                provider,
                code,
                snapshot,
                progress=lambda label: progress.emit("STAGE", code, f"indicator={label}"),
            )
            writes, snapshot_fill_count = _writes_from_fetch(fetched)
        except NavDataError as exc:
            result.error = sanitize_error(exc)
            result.error_class = _classify_error(exc, result.error)
            result.elapsed_seconds = time.monotonic() - started
            progress.emit(
                result.error_class.upper() if result.error_class == ERROR_CLASS_TIMEOUT else "FAILED",
                code,
                f"[{index}/{total}] class={result.error_class} "
                f"({result.elapsed_seconds:.1f}s) error={result.error}",
            )
            report.fund_results.append(result)
            continue
        except Exception as exc:  # noqa: BLE001 — unexpected per-fund failure
            # Includes decimal.InvalidOperation, TypeError, KeyError, etc.
            # Treat as a fund-level failure, do not crash the run.
            result.error = f"unexpected: {sanitize_error(exc)}"
            result.error_class = _classify_error(exc, result.error)
            result.elapsed_seconds = time.monotonic() - started
            progress.emit(
                "FAILED",
                code,
                f"[{index}/{total}] class={result.error_class} "
                f"({result.elapsed_seconds:.1f}s) error={result.error}",
            )
            report.fund_results.append(result)
            continue

        progress.emit("STAGE", code, "stage=db-write")
        try:
            outcome: FundWriteOutcome = record_fund_success(conn, candidate, writes)
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            # Database is down / connection broken — abort the whole run.
            raise DatabaseError(
                f"database write failed for {code}: {sanitize_error(exc)}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            # Other DB errors (constraint violations, etc.) are fund-level;
            # the attempt's transaction rolled back, nothing is persisted.
            result.error = f"database write failed: {sanitize_error(exc)}"
            result.error_class = ERROR_CLASS_DB
            result.elapsed_seconds = time.monotonic() - started
            progress.emit(
                "FAILED",
                code,
                f"[{index}/{total}] class={ERROR_CLASS_DB} "
                f"({result.elapsed_seconds:.1f}s) error={result.error}",
            )
            report.fund_results.append(result)
            continue

        result.status = STATUS_SUCCESS
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
        result.elapsed_seconds = time.monotonic() - started
        progress.emit(
            "SUCCESS",
            code,
            f"[{index}/{total}] rows={outcome.stored_rows} "
            f"last={outcome.last_data_date} ({result.elapsed_seconds:.1f}s)",
        )
        report.fund_results.append(result)

    # Target-mode reconciliation: re-read persisted maxima AFTER the attempts
    # (short read-only transaction) and list every selected eligible code
    # still below the target — including funds with no stored NAV. This is
    # deliberately separate from per-attempt success: a fund can fetch and
    # commit successfully yet remain below T, and that is NOT target
    # completion. Reconciliation read errors fail the run (fail-closed).
    if target_date is not None and report.selection_error is None:
        try:
            final_max = read_stored_max_nav_dates(
                conn, [c.code for c in selection.candidates]
            )
        except DatabaseError as exc:
            report.reconciliation_error = sanitize_error(exc)
            progress.emit(
                "RECONCILIATION-FAILED", "------", report.reconciliation_error
            )
        else:
            by_code = {r.candidate.code: r for r in report.fund_results}
            below: list[str] = []
            for c in selection.candidates:
                final = final_max.get(c.code)
                r = by_code[c.code]
                r.stored_max_nav_date = final
                if final is None or final < target_date:
                    below.append(c.code)
            report.below_target_codes = below
            progress.emit(
                "RECONCILIATION",
                "------",
                f"target={target_date.isoformat()} "
                f"selected={report.selected} attempted={report.attempted} "
                f"skipped={report.skipped} succeeded={report.succeeded} "
                f"failed={report.failed} below_target={len(below)}",
            )
    return report


__all__ = [
    "FundRunResult",
    "BackfillReport",
    "ProgressReporter",
    "run_backfill",
    "STATUS_SUCCESS",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
]
