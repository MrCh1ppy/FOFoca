"""Validation and normalization of raw AKShare directory/NAV payloads.

Everything here is pure (no I/O) so it can be unit-tested with fixtures.
Fail-closed: any unexpected shape raises ``SelectionError`` or ``NavDataError``
rather than guessing at semantics.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

import pandas as pd

from .errors import InputValidationError, NavDataError, SelectionError

# ---------------------------------------------------------------------------
# Fund code validation
# ---------------------------------------------------------------------------

_CODE_RE = re.compile(r"^\d{6}$")


def normalize_fund_code(value: object) -> str:
    """Return the canonical six-digit code or raise ``SelectionError``.

    Codes are stored exactly as the provider yields them. We accept only
    digit strings (any surrounding whitespace is stripped). A code that is not
    exactly six digits is a hard fail because padding/truncating could
    silently select a different fund.
    """
    if value is None:
        raise SelectionError("fund code is missing")
    text = str(value).strip()
    if not _CODE_RE.fullmatch(text):
        raise SelectionError(f"fund code {text!r} is not exactly six digits")
    return text


def parse_cli_fund_code(value: str) -> str:
    """Validate a ``--code`` CLI argument; raises ``InputValidationError``."""
    try:
        return normalize_fund_code(value)
    except SelectionError as exc:
        raise InputValidationError(str(exc)) from exc


# ---------------------------------------------------------------------------
# NAV numeric conversion
# ---------------------------------------------------------------------------

NUMERIC_PRECISION = 18
NUMERIC_SCALE = 6
# 10^(18-6) - 1e-6; the largest value representable in NUMERIC(18,6).
_NUMERIC_MAX = Decimal(10) ** (NUMERIC_PRECISION - NUMERIC_SCALE) - Decimal(1).scaleb(-NUMERIC_SCALE)
_QUANT = Decimal(1).scaleb(-NUMERIC_SCALE)


def nav_to_decimal(value: object) -> Decimal:
    """Convert a provider NAV value to a ``Decimal`` suitable for NUMERIC(18,6).

    Policy (per design):
      * use ``Decimal(str(value))`` so the conversion is reproducible,
      * quantize with ``ROUND_HALF_UP`` to six fractional digits,
      * reject non-finite, non-positive or out-of-range values.

    The provider already applied ``pd.to_numeric``; this is a storage policy,
    not a recovery of the original decimal precision.
    """
    if value is None:
        raise NavDataError("NAV value is None")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise NavDataError(f"NAV value is not finite: {value!r}")
        try:
            dec = Decimal(str(value))
        except InvalidOperation as exc:
            raise NavDataError(f"NAV value {value!r} cannot be converted to Decimal") from exc
    elif isinstance(value, int) and not isinstance(value, bool):
        dec = Decimal(value)
    else:
        text = str(value).strip()
        if not text:
            raise NavDataError("NAV value is an empty string")
        try:
            dec = Decimal(text)
        except InvalidOperation as exc:
            raise NavDataError(f"NAV value {text!r} is not a decimal") from exc
    if not dec.is_finite():
        raise NavDataError(f"NAV value {value!r} is not finite")
    try:
        if dec <= 0:
            raise NavDataError(f"NAV value {dec} is not positive")
        quantized = dec.quantize(_QUANT, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        # Defensive: comparisons/quantize on NaN or sNaN can also raise.
        raise NavDataError(f"NAV value {value!r} is not comparable/quantizable") from exc
    if abs(quantized) > _NUMERIC_MAX:
        raise NavDataError(f"NAV value {dec} overflows NUMERIC(18,6)")
    return quantized


def _is_blank(value: object) -> bool:
    """True for None / NaN / empty-string cells in a provider row."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


# ---------------------------------------------------------------------------
# Date handling
# ---------------------------------------------------------------------------


def parse_nav_date(value: object) -> date:
    """Parse a provider date into ``datetime.date`` or raise ``NavDataError``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        raise NavDataError("NAV date is missing")
    text = str(value).strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise NavDataError(f"NAV date {text!r} is not a YYYY-MM-DD calendar date") from exc


def parse_cli_date(value: str) -> date:
    """Validate a CLI date argument; raises ``InputValidationError``."""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise InputValidationError(f"date {value!r} is not a YYYY-MM-DD calendar date") from exc


# ---------------------------------------------------------------------------
# Directory extraction helpers
# ---------------------------------------------------------------------------


def _require_columns(df: pd.DataFrame, required: Iterable[str], source: str) -> None:
    cols = set(map(str, df.columns))
    missing = [c for c in required if c not in cols]
    if missing:
        raise SelectionError(
            f"{source}: required column(s) missing: {missing!r}; got {sorted(cols)!r}"
        )


def extract_name_directory(df: pd.DataFrame) -> dict[str, tuple[str, str]]:
    """Return ``{code: (name, fund_type)}`` from ``fund_name_em`` output.

    Fails closed on unexpected shape, missing/blank code, or duplicate codes.
    A row with a blank name or fund type is **skipped** (treated as an
    ineligible row, not a fatal schema error) so a single messy row does not
    block the entire run — such rows simply cannot be classified and so are
    excluded by the downstream filter.
    """
    source = "fund_name_em"
    if df is None:
        raise SelectionError(f"{source}: got None")
    _require_columns(df, ("基金代码", "基金简称", "基金类型"), source)
    out: dict[str, tuple[str, str]] = {}
    for _, row in df.iterrows():
        code = normalize_fund_code(row["基金代码"])
        name = str(row["基金简称"]).strip()
        fund_type_raw = row["基金类型"]
        fund_type = "" if _is_blank(fund_type_raw) else str(fund_type_raw).strip()
        if not name or not fund_type:
            # Missing/ambiguous classification on a single row: exclude that row.
            continue
        if code in out:
            raise SelectionError(f"{source}: duplicate code {code}")
        out[code] = (name, fund_type)
    return out


__all__ = [
    "NUMERIC_PRECISION",
    "NUMERIC_SCALE",
    "normalize_fund_code",
    "parse_cli_fund_code",
    "nav_to_decimal",
    "parse_nav_date",
    "parse_cli_date",
    "extract_name_directory",
    "_is_blank",
]
