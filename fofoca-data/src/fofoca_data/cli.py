"""Command-line interface for fofoca-data.

Subcommands:
  * ``init-db``  — create the three tables if missing (idempotent);
  * ``backfill`` — select funds and run the historical NAV backfill;
  * ``query``    — read-only lookup by code and inclusive date range.

Database credentials are read from the ``FOFOCA_DATABASE_URL`` environment
variable (a psycopg/libpq connection string). No credentials are stored in
the repository, and any error message printed here is sanitized so the
connection string (or its password) is never echoed to logs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg

from .backfill import run_backfill
from .db import apply_schema, query_nav_range
from .errors import DatabaseError, FofocaError, InputValidationError
from .normalize import parse_cli_date, parse_cli_fund_code
from .provider import AkshareProvider
from .redact import sanitize_error

_ENV_DB_URL = "FOFOCA_DATABASE_URL"
_ENV_DELAY = "FOFOCA_REQUEST_DELAY_SECONDS"

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


def _get_request_delay() -> float:
    raw = os.environ.get(_ENV_DELAY, "").strip()
    if not raw:
        return 1.0
    try:
        value = float(raw)
    except ValueError as exc:
        raise InputValidationError(f"{_ENV_DELAY} must be a number, got {raw!r}") from exc
    if value < 0:
        raise InputValidationError(f"{_ENV_DELAY} must be >= 0, got {value}")
    return value


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
    print("schema ensured (fund, fund_nav_daily, fund_sync_state)")
    return EXIT_OK


def _cmd_backfill(args: argparse.Namespace) -> int:
    supplied: list[str] | None = None
    if args.code:
        supplied = [parse_cli_fund_code(c) for c in args.code]

    delay = _get_request_delay()
    provider = AkshareProvider(request_delay_seconds=delay)

    with _connect() as conn:
        apply_schema(conn)
        report = run_backfill(conn, provider, supplied)

    out = {
        "ok": report.ok,
        "selection_error": report.selection_error,
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
                "error": r.error,
            }
            for r in report.fund_results
        ],
        "summary": {
            "succeeded": report.succeeded,
            "failed": report.failed,
        },
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
