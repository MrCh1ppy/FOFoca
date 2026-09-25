"""Offline unit tests for normalization helpers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from fofoca_data.errors import NavDataError, SelectionError
from fofoca_data.normalize import (
    nav_to_decimal,
    normalize_fund_code,
    parse_nav_date,
)


class TestNormalizeFundCode:
    @pytest.mark.parametrize("raw", ["000001", "110022", " 000001 ", "  519736\t"])
    def test_accepts_six_digits(self, raw: str) -> None:
        assert normalize_fund_code(raw) == raw.strip()

    @pytest.mark.parametrize(
        "raw",
        ["1", "12345", "1234567", "abcdef", "00000a", "", "  ", None, 12345],
    )
    def test_rejects_non_six_digits(self, raw) -> None:
        with pytest.raises(SelectionError):
            normalize_fund_code(raw)


class TestNavToDecimal:
    def test_plain_float(self) -> None:
        assert nav_to_decimal(1.2345) == Decimal("1.234500")

    def test_half_up_rounding(self) -> None:
        # 7th fractional digit 5 -> round up the 6th
        assert nav_to_decimal(1.2345675) == Decimal("1.234568")
        # float representation of 1.2345674 -> round down
        assert nav_to_decimal(1.2345674) == Decimal("1.234567")

    def test_half_up_on_exact_half(self) -> None:
        assert nav_to_decimal(Decimal("1.0000005")) == Decimal("1.000001")
        assert nav_to_decimal(Decimal("2.5000005")) == Decimal("2.500001")

    def test_string_input(self) -> None:
        assert nav_to_decimal("3.14") == Decimal("3.140000")

    def test_int_input(self) -> None:
        assert nav_to_decimal(2) == Decimal("2.000000")

    @pytest.mark.parametrize("bad", [0, -1.0, 0.0, Decimal("0"), "0", "-0.5"])
    def test_rejects_non_positive(self, bad) -> None:
        with pytest.raises(NavDataError):
            nav_to_decimal(bad)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "nan", "inf", None, ""])
    def test_rejects_non_finite_or_empty(self, bad) -> None:
        with pytest.raises(NavDataError):
            nav_to_decimal(bad)

    @pytest.mark.parametrize(
        "bad",
        [
            Decimal("NaN"),
            Decimal("Infinity"),
            Decimal("-Infinity"),
            Decimal("sNaN"),  # signaling NaN — quantize/comparison can raise InvalidOperation
        ],
    )
    def test_rejects_non_finite_decimal(self, bad) -> None:
        """Decimal NaN/±Inf must raise NavDataError, not raw InvalidOperation."""
        with pytest.raises(NavDataError):
            nav_to_decimal(bad)

    def test_rejects_overflow(self) -> None:
        # 12 integer digits + 6 fractional digits = precision 18, the max.
        # Anything bigger must be rejected.
        with pytest.raises(NavDataError):
            nav_to_decimal(Decimal("999999999999.9999995"))  # rounds to 13 integer digits
        with pytest.raises(NavDataError):
            nav_to_decimal(Decimal("1000000000000"))

    def test_accepts_upper_bound(self) -> None:
        assert nav_to_decimal(Decimal("999999999999.999999")) == Decimal("999999999999.999999")


class TestParseNavDate:
    def test_iso_string(self) -> None:
        assert parse_nav_date("2024-03-05") == date(2024, 3, 5)

    def test_strips_whitespace(self) -> None:
        assert parse_nav_date(" 2024-03-05 ") == date(2024, 3, 5)

    def test_accepts_date_and_datetime(self) -> None:
        from datetime import datetime

        assert parse_nav_date(date(2024, 3, 5)) == date(2024, 3, 5)
        assert parse_nav_date(datetime(2024, 3, 5, 12, 0)) == date(2024, 3, 5)

    @pytest.mark.parametrize("bad", ["2024-13-01", "2024-02-30", "not-a-date", "", None, "2024/03/05"])
    def test_rejects_invalid(self, bad) -> None:
        with pytest.raises(NavDataError):
            parse_nav_date(bad)
