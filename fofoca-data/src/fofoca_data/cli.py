"""Command-line interface for fofoca-data.

Subcommands:
  * ``init-db``  — create the two tables if missing (idempotent, never
    drops anything);
  * ``backfill`` — select funds and run the historical NAV backfill
    (optionally resumable via ``--target-date``);
  * ``query``    — read-only lookup by code and inclusive date range.

Removing the legacy ``fund_sync_state`` table from an existing database is a
**manual, DBA-run** migration (verified external backup + explicit SQL),
documented in ``deploy/README.md``. It is deliberately NOT a CLI command:
no application code path may drop tables.

Database credentials are read from the ``FOFOCA_DATABASE_URL`` environment
variable (a psycopg/libpq connection string). No credentials are stored in
the repository, and any error message printed here is sanitized so the
connection string (or its password) is never echoed to logs.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

import psycopg

from .backfill import ProgressReporter, run_backfill
from .db import apply_schema, query_nav_range
from .errors import DatabaseError, FofocaError, InputValidationError
from .normalize import parse_cli_date, parse_cli_fund_code
from .provider import AkshareProvider
from .redact import sanitize_error

_ENV_DB_URL = "FOFOCA_DATABASE_URL"
_ENV_DELAY = "FOFOCA_REQUEST_DELAY_SECONDS"
_ENV_CONNECT_TIMEOUT = "FOFOCA_CONNECT_TIMEOUT_SECONDS"
_ENV_READ_TIMEOUT = "FOFOCA_READ_TIMEOUT_SECONDS"

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3


def _get_database_url() -> str:
    url = os.environ.get(_ENV_DB_URL, "").strip()
    if not url:
        raise InputValidationError(
            f"{_ENV_DB_URL} is not set; provide a psycopg connection string "
            "(e.g. postgresql://user:pass@host:5432/fofoca)"
        )
    return url


def _get_positive_float_env(name: str, default: float, *, allow_zero: bool = False) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise InputValidationError(f"{name} must be a number, got {raw!r}") from exc
    # NaN/inf must never pass: a NaN timeout silently disables the bound and
    # an infinite one is equivalent to no bound at all.
    if not math.isfinite(value):
        raise InputValidationError(f"{name} must be finite, got {raw!r}")
    if allow_zero:
        if value < 0:
            raise InputValidationError(f"{name} must be >= 0, got {value}")
    elif value <= 0:
        raise InputValidationError(f"{name} must be > 0, got {value}")
    return value


def _get_request_delay() -> float:
    return _get_positive_float_env(_ENV_DELAY, 1.0, allow_zero=True)


def _get_connect_timeout() -> float:
    from .provider import DEFAULT_CONNECT_TIMEOUT_SECONDS

    return _get_positive_float_env(_ENV_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT_SECONDS)


def _get_read_timeout() -> float:
    from .provider import DEFAULT_READ_TIMEOUT_SECONDS

    return _get_positive_float_env(_ENV_READ_TIMEOUT, DEFAULT_READ_TIMEOUT_SECONDS)


def _connect() -> psycopg.Connection:
    url = _get_database_url()  # raises InputValidationError if missing
    try:
        return psycopg.connect(url, autocommit=False)
    except Exception as exc:  # noqa: BLE001
        # Sanitize so the DSN (with password) never reaches stderr/logs.
        raise DatabaseError(
            f"could not connect to database: {sanitize_error(exc)}"
        ) from exc


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_init_db(_args: argparse.Namespace) -> int:
    with _connect() as conn:
        apply_schema(conn)
    print("schema ensured (fund, fund_nav_daily)")
    return EXIT_OK


def _cmd_backfill(args: argparse.Namespace) -> int:
    supplied: list[str] | None = None
    if args.code:
        supplied = [parse_cli_fund_code(c) for c in args.code]

    target_date = None
    if args.target_date is not None:
        target_date = parse_cli_date(args.target_date)

    delay = _get_request_delay()
    provider = AkshareProvider(
        request_delay_seconds=delay,
        connect_timeout_seconds=_get_connect_timeout(),
        read_timeout_seconds=_get_read_timeout(),
    )

    progress = ProgressReporter(sys.stderr)
    with provider, _connect() as conn:
        apply_schema(conn)
        report = run_backfill(
            conn,
            provider,
            supplied,
            target_date=target_date,
            progress=progress,
        )

    out = {
        "ok": report.ok,
        "target_date": report.target_date.isoformat() if report.target_date else None,
        "selection_error": report.selection_error,
        "reconciliation_error": report.reconciliation_error,
        "selection": (
            {
                "counts": report.selection.counts.as_dict(),
                "ineligible_supplied_codes": report.selection.ineligible_supplied_codes,
                "selected": [c.code for c in report.selection.candidates],
                "snapshot_dates": [
                    d.isoformat() for d in report.selection.daily_snapshot.dates
                ],
            }
            if report.selection
            else None
        ),
        "fund_results": [
            {
                "code": r.candidate.code,
                "name": r.candidate.name,
                "status": r.status,
                "partial_coverage": r.partial_coverage,
                "snapshot_fill_count": r.snapshot_fill_count,
                "discrepancy_count": r.discrepancy_count,
                "discrepancies": r.discrepancies,
                "stored_rows": r.stored_rows,
                "first_data_date": (
                    r.first_data_date.isoformat() if r.first_data_date else None
                ),
                "last_data_date": (
                    r.last_data_date.isoformat() if r.last_data_date else None
                ),
                "stored_max_nav_date": (
                    r.stored_max_nav_date.isoformat() if r.stored_max_nav_date else None
                ),
                "error_class": r.error_class,
                "error": r.error,
                "elapsed_seconds": round(r.elapsed_seconds, 3),
            }
            for r in report.fund_results
        ],
        "summary": {
            "selected": report.selected,
            "attempted": report.attempted,
            "skipped": report.skipped,
            "succeeded": report.succeeded,
            "failed": report.failed,
            "below_target": (
                len(report.below_target_codes)
                if report.below_target_codes is not None
                else None
            ),
        },
        "below_target_codes": report.below_target_codes,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return EXIT_OK if report.ok else EXIT_RUN_FAILED


def _cmd_query(args: argparse.Namespace) -> int:
    code = parse_cli_fund_code(args.code)
    start = parse_cli_date(args.start)
    end = parse_cli_date(args.end)
    if start > end:
        raise InputValidationError(
            f"start date {start.isoformat()} is after end date {end.isoformat()}"
        )

    with _connect() as conn:
        rows = query_nav_range(conn, code, start, end)

    out = {
        "code": code,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rows": [
            {
                "nav_date": r.nav_date.isoformat(),
                "unit_nav": (str(r.unit_nav) if r.unit_nav is not None else None),
                "accumulated_nav": (
                    str(r.accumulated_nav) if r.accumulated_nav is not None else None
                ),
            }
            for r in rows
        ],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fofoca-data",
        description=(
            "Historical NAV backfill for eligible open-purchase/open-redemption "
            "Chinese mutual funds (one-off, unscheduled)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="create tables if missing (idempotent)")
    p_init.set_defaults(func=_cmd_init_db)

    p_backfill = sub.add_parser("backfill", help="run the historical backfill")
    p_backfill.add_argument(
        "--code",
        action="append",
        default=None,
        metavar="FUND_CODE",
        help=(
            "six-digit fund code; may be repeated. If omitted, all current "
            "eligible candidates are selected. Supplied codes still must pass "
            "the eligibility check."
        ),
    )
    p_backfill.add_argument(
        "--target-date",
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "optional real calendar date. For each selected fund, read the "
            "stored MAX(nav_date); if it is at least the target, skip the "
            "fund before either history fetch (no writes). Without this flag "
            "every eligible selected fund gets a full-history re-fetch, even "
            "if its stored max is later. The target is a work threshold, not "
            "proof of contiguous or correct history. The documented recovery "
            "invocation uses --target-date 2026-09-24."
        ),
    )
    p_backfill.set_defaults(func=_cmd_backfill)

    p_query = sub.add_parser("query", help="read-only NAV lookup")
    p_query.add_argument("--code", required=True, help="six-digit fund code")
    p_query.add_argument("--start", required=True, help="inclusive start date YYYY-MM-DD")
    p_query.add_argument("--end", required=True, help="inclusive end date YYYY-MM-DD")
    p_query.set_defaults(func=_cmd_query)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except InputValidationError as exc:
        print(f"input error: {sanitize_error(exc)}", file=sys.stderr)
        return EXIT_USAGE
    except DatabaseError as exc:
        print(f"database error: {sanitize_error(exc)}", file=sys.stderr)
        return EXIT_RUN_FAILED
    except FofocaError as exc:
        print(f"error: {sanitize_error(exc)}", file=sys.stderr)
        return EXIT_RUN_FAILED
    except Exception as exc:  # noqa: BLE001
        # Defensive: never echo a raw traceback containing the DSN.
        print(f"unexpected error: {sanitize_error(exc)}", file=sys.stderr)
        return EXIT_RUN_FAILED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
