"""Fetch the two full-history NAV indicators and reconcile with the daily snapshot.

History semantics (per spec):
  * both ``fund_open_fund_info_em`` indicators are requested independently,
    without date filters;
  * the daily snapshot's dated columns (``YYYY-MM-DD-单位净值`` /
    ``YYYY-MM-DD-累计净值``) may supply values **only** for date/metric slots
    that history left empty;
  * a non-NULL historical value always wins over a snapshot value for the
    same date/metric; the disagreement is reported, not silently overwritten;
  * genuinely missing historical cells (``None`` / ``NaN``) are treated as
    absent on otherwise valid rows — dates are still validated and duplicate
    dates still fail; malformed/blank-string, infinite, non-positive or
    overflowing **present** values fail the fund;
  * a failed history request or a response with no usable NAV on **both**
    indicators fails the fund even if snapshot values exist; if only one
    indicator has usable NAV the fund reports partial coverage;
  * a (date, metric) slot with no usable historical value and no valid dated
    snapshot fill yields no stored value for that slot — missing metrics
    never erase stored non-NULL values;
  * malformed dated snapshot columns or invalid present snapshot NAV values
    fail the fund (fail-closed).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pandas as pd

from .eligibility import DailySnapshot
from .errors import NavDataError
from .normalize import nav_to_decimal, parse_nav_date
from .provider import Provider

UNIT_INDICATOR = "单位净值走势"
ACCUMULATED_INDICATOR = "累计净值走势"

_UNIT_DATE_COL = "净值日期"
_UNIT_VALUE_COL = "单位净值"
_ACC_DATE_COL = "净值日期"
_ACC_VALUE_COL = "累计净值"

ProgressCallback = Callable[[str], None]


@dataclass
class NavPoint:
    nav_date: date
    unit_nav: Decimal | None = None
    accumulated_nav: Decimal | None = None


@dataclass(frozen=True)
class Discrepancy:
    """Same-date, same-metric disagreement between history and snapshot."""

    nav_date: date
    metric: str  # "unit_nav" | "accumulated_nav"
    historical: Decimal
    snapshot: Decimal


@dataclass
class FundNavFetch:
    """Result of fetching both history indicators for one fund."""

    historical_points: list[NavPoint] = field(default_factory=list)
    snapshot_points: list[NavPoint] = field(default_factory=list)
    unit_indicator_empty: bool = False
    accumulated_indicator_empty: bool = False
    discrepancies: list[Discrepancy] = field(default_factory=list)

    @property
    def partial_coverage(self) -> bool:
        """True when exactly one history indicator was valid-empty."""
        return self.unit_indicator_empty != self.accumulated_indicator_empty

    @property
    def both_empty(self) -> bool:
        return self.unit_indicator_empty and self.accumulated_indicator_empty

    @property
    def merged_points(self) -> list[NavPoint]:
        """History points outer-joined with snapshot-only gap fills.

        Snapshot values are only used for (date, metric) slots where the
        historical value is missing. The result is sorted by date.
        """
        by_date: dict[date, NavPoint] = {p.nav_date: p for p in self.historical_points}
        # Deterministic merge so reruns produce identical output.
        for snap in sorted(self.snapshot_points, key=lambda p: p.nav_date):
            existing = by_date.get(snap.nav_date)
            if existing is None:
                by_date[snap.nav_date] = NavPoint(
                    nav_date=snap.nav_date,
                    unit_nav=snap.unit_nav,
                    accumulated_nav=snap.accumulated_nav,
                )
                continue
            if existing.unit_nav is None and snap.unit_nav is not None:
                existing.unit_nav = snap.unit_nav
            if existing.accumulated_nav is None and snap.accumulated_nav is not None:
                existing.accumulated_nav = snap.accumulated_nav
        return [by_date[d] for d in sorted(by_date)]


def _series_from_indicator(
    df: pd.DataFrame,
    *,
    date_col: str,
    value_col: str,
    label: str,
) -> dict[date, Decimal]:
    """Normalize one indicator DataFrame into ``{date: Decimal}`` of usable NAVs.

    ``df`` may be empty or ``None``-valued in the value column. Genuinely
    missing cells (``None`` / pandas ``NaN``) are treated as absent: their
    dates are still parsed and validated, and duplicate dates still fail
    closed, but they contribute no value. Present values must parse and pass
    the full NAV policy (finite, positive, in range). Any duplicate date
    (even on missing-value rows) fails closed.
    """
    if df is None:
        raise NavDataError(f"{label}: provider returned None")
    if df.empty:
        return {}
    missing = [c for c in (date_col, value_col) if c not in df.columns]
    if missing:
        raise NavDataError(
            f"{label}: required column(s) missing: {missing!r}; got {list(df.columns)!r}"
        )
    out: dict[date, Decimal] = {}
    seen: set[date] = set()
    for _, row in df.iterrows():
        try:
            nav_date = parse_nav_date(row[date_col])
        except NavDataError:
            raise
        except Exception as exc:
            raise NavDataError(
                f"{label}: row date could not be parsed "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        if nav_date in seen:
            raise NavDataError(f"{label}: duplicate date {nav_date.isoformat()}")
        seen.add(nav_date)
        raw_value = row[value_col]
        if raw_value is None or (isinstance(raw_value, float) and math.isnan(raw_value)):
            # Genuinely missing cell: date validated above, no value stored.
            continue
        try:
            out[nav_date] = nav_to_decimal(raw_value)
        except NavDataError:
            raise
        except Exception as exc:
            # Defensive: InvalidOperation on signaling NaN Decimal etc.
            raise NavDataError(
                f"{label}: row could not be parsed "
                f"({type(exc).__name__}: {exc})"
            ) from exc
    return out


def _snapshot_points_for(
    code: str, snapshot: DailySnapshot | None
) -> list[NavPoint]:
    """Convert this fund's daily-snapshot points to ``NavPoint`` records.

    Each snapshot value must pass the same NAV validation as history;
    invalid values fail the fund.
    """
    if snapshot is None:
        return []
    raw_points = snapshot.points.get(code, [])
    out: list[NavPoint] = []
    for sp in raw_points:
        unit = None
        accumulated = None
        if sp.unit_nav is not None:
            try:
                unit = nav_to_decimal(sp.unit_nav)
            except NavDataError as exc:
                raise NavDataError(
                    f"{code}: invalid 单位净值 in daily snapshot column for "
                    f"{sp.nav_date.isoformat()}: {exc}"
                ) from exc
            except Exception as exc:
                # Defensive: InvalidOperation on signaling NaN etc.
                raise NavDataError(
                    f"{code}: invalid 单位净值 in daily snapshot column for "
                    f"{sp.nav_date.isoformat()}: unexpected {type(exc).__name__}: {exc}"
                ) from exc
        if sp.accumulated_nav is not None:
            try:
                accumulated = nav_to_decimal(sp.accumulated_nav)
            except NavDataError as exc:
                raise NavDataError(
                    f"{code}: invalid 累计净值 in daily snapshot column for "
                    f"{sp.nav_date.isoformat()}: {exc}"
                ) from exc
            except Exception as exc:
                raise NavDataError(
                    f"{code}: invalid 累计净值 in daily snapshot column for "
                    f"{sp.nav_date.isoformat()}: unexpected {type(exc).__name__}: {exc}"
                ) from exc
        if unit is None and accumulated is None:
            continue
        out.append(
            NavPoint(nav_date=sp.nav_date, unit_nav=unit, accumulated_nav=accumulated)
        )
    return out


def fetch_fund_nav(
    provider: Provider,
    code: str,
    snapshot: DailySnapshot | None = None,
    progress: ProgressCallback | None = None,
) -> FundNavFetch:
    """Fetch both history indicators for ``code`` and reconcile with the snapshot.

    Raises ``NavDataError`` on history request failure, malformed nonempty
    history, both history indicators having no usable NAV, or an invalid
    snapshot value for a (date, metric) the snapshot actually exposes for
    this fund. ``progress``, when given, is invoked with the indicator label
    immediately before each request so callers can log per-stage timing.
    """
    _notify = progress if progress is not None else lambda _label: None
    try:
        _notify(UNIT_INDICATOR)
        unit_df = provider.fund_open_fund_info_em(code, UNIT_INDICATOR)
    except NavDataError:
        raise
    except Exception as exc:  # noqa: BLE001
        # Any provider-side failure (network, unexpected exception) is a
        # per-fund failure, not a run-crash.
        raise NavDataError(f"{code}: 单位净值走势 request failed: {exc}") from exc
    try:
        _notify(ACCUMULATED_INDICATOR)
        acc_df = provider.fund_open_fund_info_em(code, ACCUMULATED_INDICATOR)
    except NavDataError:
        raise
    except Exception as exc:
        raise NavDataError(f"{code}: 累计净值走势 request failed: {exc}") from exc

    unit = _series_from_indicator(
        unit_df, date_col=_UNIT_DATE_COL, value_col=_UNIT_VALUE_COL, label="单位净值走势"
    )
    accumulated = _series_from_indicator(
        acc_df, date_col=_ACC_DATE_COL, value_col=_ACC_VALUE_COL, label="累计净值走势"
    )

    unit_empty = len(unit) == 0
    acc_empty = len(accumulated) == 0
    if unit_empty and acc_empty:
        raise NavDataError(
            f"{code}: both NAV indicators returned no usable historical NAV values"
        )

    all_dates = sorted(set(unit) | set(accumulated))
    historical_points = [
        NavPoint(
            nav_date=d,
            unit_nav=unit.get(d),
            accumulated_nav=accumulated.get(d),
        )
        for d in all_dates
    ]

    snapshot_points = _snapshot_points_for(code, snapshot)

    discrepancies: list[Discrepancy] = []
    hist_by_date = {p.nav_date: p for p in historical_points}
    for snap in snapshot_points:
        hist = hist_by_date.get(snap.nav_date)
        if hist is None:
            continue
        if (
            snap.unit_nav is not None
            and hist.unit_nav is not None
            and snap.unit_nav != hist.unit_nav
        ):
            discrepancies.append(
                Discrepancy(
                    nav_date=snap.nav_date,
                    metric="unit_nav",
                    historical=hist.unit_nav,
                    snapshot=snap.unit_nav,
                )
            )
        if (
            snap.accumulated_nav is not None
            and hist.accumulated_nav is not None
            and snap.accumulated_nav != hist.accumulated_nav
        ):
            discrepancies.append(
                Discrepancy(
                    nav_date=snap.nav_date,
                    metric="accumulated_nav",
                    historical=hist.accumulated_nav,
                    snapshot=snap.accumulated_nav,
                )
            )

    return FundNavFetch(
        historical_points=historical_points,
        snapshot_points=snapshot_points,
        unit_indicator_empty=unit_empty,
        accumulated_indicator_empty=acc_empty,
        discrepancies=discrepancies,
    )


__all__ = [
    "UNIT_INDICATOR",
    "ACCUMULATED_INDICATOR",
    "NavPoint",
    "FundNavFetch",
    "Discrepancy",
    "fetch_fund_nav",
]
