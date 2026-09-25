"""Eligibility selection for fofoca-data.

Implements the conservative current-candidate rule from the spec:

  candidate = intersection(fund_name_em codes, fund_open_fund_daily_em codes)
              AND daily 申购状态 == "开放申购"
              AND daily 赎回状态 == "开放赎回"
              AND name-directory 基金类型 does not start with "货币型"

There is deliberately **no** ETF/LOF name filter and **no** spot-feed
exclusion: an open-on-both-sides LOF such as ``166009`` qualifies, and an
exchange-traded ETF with both sides open may also qualify. This is a
pragmatic NAV-based subscription/redemption proxy for this platform, not
proof of off-exchange trading or Alipay availability.

Both required feeds must be present and parseable; otherwise selection fails
closed before any fund is processed. Filter order is fixed so the auditable
counts do not double-count codes that fail multiple filters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from .errors import SelectionError
from .normalize import _is_blank, extract_name_directory, normalize_fund_code
from .provider import Provider

MONEY_MARKET_TYPE_PREFIX = "货币型"
REQUIRED_PURCHASE_STATUS = "开放申购"
REQUIRED_REDEMPTION_STATUS = "开放赎回"

# Daily-snapshot dated NAV columns look like "2026-09-24-单位净值".
_DATED_NAV_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(单位净值|累计净值)$")

_DAILY_REQUIRED_COLUMNS = ("基金代码", "申购状态", "赎回状态")


@dataclass(frozen=True)
class FundCandidate:
    code: str
    name: str
    fund_type: str


@dataclass
class SelectionCounts:
    """Deterministic, auditable filter counts (fixed order)."""

    name_directory_size: int = 0
    daily_directory_size: int = 0
    name_rows_skipped_missing_type: int = 0
    daily_rows_skipped_missing_status: int = 0
    intersection_size: int = 0
    excluded_purchase_status: int = 0
    excluded_redemption_status: int = 0
    excluded_money_market: int = 0
    final_candidates: int = 0
    supplied_ineligible: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "name_directory_size": self.name_directory_size,
            "daily_directory_size": self.daily_directory_size,
            "name_rows_skipped_missing_type": self.name_rows_skipped_missing_type,
            "daily_rows_skipped_missing_status": self.daily_rows_skipped_missing_status,
            "intersection_size": self.intersection_size,
            "excluded_purchase_status": self.excluded_purchase_status,
            "excluded_redemption_status": self.excluded_redemption_status,
            "excluded_money_market": self.excluded_money_market,
            "final_candidates": self.final_candidates,
            "supplied_ineligible": self.supplied_ineligible,
        }


@dataclass
class SelectionResult:
    candidates: list[FundCandidate]
    ineligible_supplied_codes: list[str]
    counts: SelectionCounts
    daily_snapshot: "DailySnapshot"


@dataclass(frozen=True)
class SnapshotPoint:
    """One (date, metric) NAV value from a daily-snapshot column."""

    nav_date: date
    unit_nav: object | None  # Decimal | None, but kept opaque here
    accumulated_nav: object | None


@dataclass
class DailySnapshot:
    """Parsed view of ``fund_open_fund_daily_em``.

    Holds:
      * ``status``: ``{code: (purchase_status, redemption_status)}``
      * ``points``: ``{code: list[SnapshotPoint]}`` for the latest two distinct
        actual dates present in the dated columns
      * ``dates``: the discovered distinct dates in descending order (max 2)
      * ``raw_size``: number of data rows in the source DataFrame
      * ``skipped_missing_status``: rows dropped because 申购状态 or 赎回状态
        was blank (these rows cannot be classified and are excluded from
        candidacy, but they still count toward the raw feed size).
    """

    status: dict[str, tuple[str, str]]
    points: dict[str, list["SnapshotPoint"]]
    dates: list[date]
    raw_size: int = 0
    skipped_missing_status: int = 0


def _parse_dated_column(name: str) -> tuple[date, str] | None:
    m = _DATED_NAV_RE.match(name)
    if not m:
        return None
    date_text, metric = m.group(1), m.group(2)
    try:
        nav_date = datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SelectionError(
            f"fund_open_fund_daily_em: invalid calendar date in column {name!r}"
        ) from exc
    return nav_date, metric


def parse_daily_snapshot(df: pd.DataFrame) -> DailySnapshot:
    """Validate and parse ``fund_open_fund_daily_em`` output.

    Required static columns: ``基金代码``, ``申购状态``, ``赎回状态``.
    Dated NAV columns must match ``YYYY-MM-DD-单位净值`` / ``YYYY-MM-DD-累计净值``
    and contain a real calendar date. Fee fields are read but ignored.

    Any duplicate code, blank required status, malformed dated column name,
    or invalid calendar date in a dated column is a hard fail (fail-closed).
    """
    source = "fund_open_fund_daily_em"
    if df is None:
        raise SelectionError(f"{source}: got None")

    cols = [str(c) for c in df.columns]
    col_set = set(cols)
    for c in _DAILY_REQUIRED_COLUMNS:
        if c not in col_set:
            raise SelectionError(
                f"{source}: required column {c!r} missing; got {sorted(col_set)!r}"
            )

    dated: list[tuple[str, date, str]] = []  # (column_name, date, metric)
    for c in cols:
        if c in _DAILY_REQUIRED_COLUMNS:
            continue
        parsed = _parse_dated_column(c)
        if parsed is None:
            # Non-dated auxiliary columns (基金简称, 日增长值, 日增长率, 手续费, ...) are ignored.
            continue
        nav_date, metric = parsed
        dated.append((c, nav_date, metric))

    if not dated:
        raise SelectionError(
            f"{source}: no dated NAV columns matching YYYY-MM-DD-单位净值/累计净值 found"
        )

    distinct_dates = sorted({d for _, d, _ in dated}, reverse=True)
    # Keep at most the two latest distinct dates.
    used_dates = distinct_dates[:2]
    used_date_set = set(used_dates)

    # (code) -> {date: {metric: raw_value}}
    raw_points: dict[str, dict[date, dict[str, object]]] = {}
    status: dict[str, tuple[str, str]] = {}
    raw_size = len(df)
    skipped_missing_status = 0
    for _, row in df.iterrows():
        code = normalize_fund_code(row["基金代码"])
        purchase = str(row["申购状态"]).strip()
        redeem = str(row["赎回状态"]).strip()
        if not purchase or not redeem:
            # Blank status on a single row: exclude that row, do not abort.
            skipped_missing_status += 1
            continue
        if code in status:
            raise SelectionError(f"{source}: duplicate code {code}")
        status[code] = (purchase, redeem)

        per_date: dict[date, dict[str, object]] = {}
        for col, nav_date, metric in dated:
            if nav_date not in used_date_set:
                continue
            value = row[col]
            if _is_blank(value):
                continue
            per_date.setdefault(nav_date, {})[metric] = value
        if per_date:
            raw_points[code] = per_date

    points: dict[str, list[SnapshotPoint]] = {}
    for code, per_date in raw_points.items():
        pts = []
        for d in sorted(per_date):
            metrics = per_date[d]
            pts.append(
                SnapshotPoint(
                    nav_date=d,
                    unit_nav=metrics.get("单位净值"),
                    accumulated_nav=metrics.get("累计净值"),
                )
            )
        points[code] = pts

    return DailySnapshot(
        status=status,
        points=points,
        dates=used_dates,
        raw_size=raw_size,
        skipped_missing_status=skipped_missing_status,
    )


@dataclass(frozen=True)
class _DirectoryBundle:
    name_dir: dict[str, tuple[str, str]]
    daily: DailySnapshot
    name_raw_size: int
    name_skipped_missing_type: int


def _fetch_directories(provider: Provider) -> _DirectoryBundle:
    """Fetch and validate both mandatory feeds, fail-closed."""
    try:
        name_df = provider.fund_name_em()
    except Exception as exc:  # noqa: BLE001
        raise SelectionError(f"fund_name_em request failed: {exc}") from exc
    try:
        daily_df = provider.fund_open_fund_daily_em()
    except Exception as exc:
        raise SelectionError(f"fund_open_fund_daily_em request failed: {exc}") from exc

    name_raw_size = 0 if name_df is None else len(name_df)
    name_dir = extract_name_directory(name_df)
    name_skipped_missing_type = name_raw_size - len(name_dir)
    daily = parse_daily_snapshot(daily_df)
    return _DirectoryBundle(
        name_dir=name_dir,
        daily=daily,
        name_raw_size=name_raw_size,
        name_skipped_missing_type=name_skipped_missing_type,
    )


def _classify_all(bundle: _DirectoryBundle, counts: SelectionCounts) -> dict[str, FundCandidate]:
    """Apply the deterministic filter pipeline and return eligible candidates keyed by code."""
    counts.name_directory_size = bundle.name_raw_size
    counts.daily_directory_size = bundle.daily.raw_size
    counts.name_rows_skipped_missing_type = bundle.name_skipped_missing_type
    counts.daily_rows_skipped_missing_status = bundle.daily.skipped_missing_status

    intersection = sorted(set(bundle.name_dir) & set(bundle.daily.status))
    counts.intersection_size = len(intersection)

    eligible: dict[str, FundCandidate] = {}
    for code in intersection:
        name, fund_type = bundle.name_dir[code]
        purchase_status, redemption_status = bundle.daily.status[code]

        # Fixed filter order; first failing filter claims the code.
        if purchase_status != REQUIRED_PURCHASE_STATUS:
            counts.excluded_purchase_status += 1
            continue
        if redemption_status != REQUIRED_REDEMPTION_STATUS:
            counts.excluded_redemption_status += 1
            continue
        if fund_type.startswith(MONEY_MARKET_TYPE_PREFIX):
            counts.excluded_money_market += 1
            continue
        eligible[code] = FundCandidate(code=code, name=name, fund_type=fund_type)

    counts.final_candidates = len(eligible)
    return eligible


def select_candidates(
    provider: Provider,
    supplied_codes: list[str] | None = None,
) -> SelectionResult:
    """Select funds for backfill.

    * ``supplied_codes is None`` or empty list → all current eligible candidates.
    * otherwise → only those supplied codes that pass the same eligibility
      check; ineligible supplied codes are reported, not fetched.
    """
    counts = SelectionCounts()
    bundle = _fetch_directories(provider)
    eligible_by_code = _classify_all(bundle, counts)

    if not supplied_codes:
        candidates = [eligible_by_code[c] for c in sorted(eligible_by_code)]
        return SelectionResult(
            candidates=candidates,
            ineligible_supplied_codes=[],
            counts=counts,
            daily_snapshot=bundle.daily,
        )

    normalized_supplied: list[str] = []
    for raw in supplied_codes:
        normalized_supplied.append(normalize_fund_code(raw))

    selected: list[FundCandidate] = []
    ineligible: list[str] = []
    seen: set[str] = set()
    for code in normalized_supplied:
        if code in seen:
            continue
        seen.add(code)
        candidate = eligible_by_code.get(code)
        if candidate is None:
            ineligible.append(code)
        else:
            selected.append(candidate)

    counts.supplied_ineligible = len(ineligible)
    counts.final_candidates = len(selected)
    return SelectionResult(
        candidates=selected,
        ineligible_supplied_codes=ineligible,
        counts=counts,
        daily_snapshot=bundle.daily,
    )


__all__ = [
    "FundCandidate",
    "SelectionCounts",
    "SelectionResult",
    "DailySnapshot",
    "SnapshotPoint",
    "select_candidates",
    "parse_daily_snapshot",
    "MONEY_MARKET_TYPE_PREFIX",
    "REQUIRED_PURCHASE_STATUS",
    "REQUIRED_REDEMPTION_STATUS",
]
