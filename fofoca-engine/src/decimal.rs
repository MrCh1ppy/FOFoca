//! Nonnegative fixed-scale decimals. Integer checks precede Decimal construction;
//! no rust_decimal arithmetic/rescaling is allowed to round a financial value.
use std::{fmt, str::FromStr};

use rust_decimal::Decimal;
use snafu::Snafu;

const MAX_COEFFICIENT: u128 = (1_u128 << 96) - 1;

#[derive(Debug, Snafu, PartialEq, Eq)]
pub enum DecimalError {
    #[snafu(display("Expected an unsigned ASCII decimal without leading zeros"))]
    Format,
    #[snafu(display("At most {maximum} supplied fractional digits are allowed"))]
    Scale { maximum: u32 },
    #[snafu(display("Value must be strictly positive"))]
    NonPositive,
    #[snafu(display("Exact fixed-scale decimal coefficient exceeds 96 bits"))]
    Overflow,
    #[snafu(display("Subtraction would produce a negative value"))]
    NegativeResult,
}

fn parse(input: &str, scale: u32) -> Result<Decimal, DecimalError> {
    let (whole, fraction) = match input.split_once('.') {
        Some((whole, fraction)) if !fraction.is_empty() => (whole, fraction),
        Some(_) => return Err(DecimalError::Format),
        None => (input, ""),
    };
    if whole.is_empty()
        || (whole.len() > 1 && whole.starts_with('0'))
        || !whole
            .bytes()
            .chain(fraction.bytes())
            .all(|b| b.is_ascii_digit())
    {
        return Err(DecimalError::Format);
    }
    if fraction.len() > scale as usize {
        return Err(DecimalError::Scale { maximum: scale });
    }
    let coefficient = whole
        .bytes()
        .chain(fraction.bytes())
        .try_fold(0_u128, |n, b| {
            n.checked_mul(10)
                .and_then(|n| n.checked_add(u128::from(b - b'0')))
                .ok_or(DecimalError::Overflow)
        })?;
    let coefficient = coefficient
        .checked_mul(10_u128.pow(scale - fraction.len() as u32))
        .ok_or(DecimalError::Overflow)?;
    from_coefficient(coefficient, scale)
}

fn from_coefficient(coefficient: u128, scale: u32) -> Result<Decimal, DecimalError> {
    if coefficient > MAX_COEFFICIENT {
        return Err(DecimalError::Overflow);
    }
    Ok(Decimal::from_i128_with_scale(coefficient as i128, scale))
}

macro_rules! fixed_decimal {
    ($name:ident, $scale:literal) => {
        #[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
        pub struct $name(Decimal);

        impl $name {
            pub const ZERO: Self = Self(Decimal::from_parts(0, 0, 0, false, $scale));

            pub fn parse(input: &str) -> Result<Self, DecimalError> {
                parse(input, $scale).map(Self)
            }

            pub fn checked_add(self, rhs: Self) -> Result<Self, DecimalError> {
                from_coefficient(self.coefficient() + rhs.coefficient(), $scale).map(Self)
            }

            pub fn checked_sub(self, rhs: Self) -> Result<Self, DecimalError> {
                let coefficient = self
                    .coefficient()
                    .checked_sub(rhs.coefficient())
                    .ok_or(DecimalError::NegativeResult)?;
                from_coefficient(coefficient, $scale).map(Self)
            }

            fn coefficient(self) -> u128 {
                self.0.mantissa() as u128
            }
        }

        impl FromStr for $name {
            type Err = DecimalError;
            fn from_str(input: &str) -> Result<Self, Self::Err> {
                Self::parse(input)
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                // Canonical output must not inherit a caller's rounding precision.
                write!(formatter, "{}", self.0)
            }
        }
    };
}

fixed_decimal!(Money, 12);
fixed_decimal!(Shares, 6);

impl Shares {
    /// External order quantities must be positive; internal positions may be zero.
    pub fn parse_positive(input: &str) -> Result<Self, DecimalError> {
        let shares = Self::parse(input)?;
        if shares == Self::ZERO {
            return Err(DecimalError::NonPositive);
        }
        Ok(shares)
    }

    pub fn checked_value(self, nav: Nav) -> Result<Money, DecimalError> {
        let coefficient = self
            .coefficient()
            .checked_mul(nav.0.coefficient())
            .ok_or(DecimalError::Overflow)?;
        from_coefficient(coefficient, 12).map(Money)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct Nav(Shares);

impl Nav {
    pub fn parse(input: &str) -> Result<Self, DecimalError> {
        Shares::parse_positive(input).map(Self)
    }
}

impl FromStr for Nav {
    type Err = DecimalError;
    fn from_str(input: &str) -> Result<Self, Self::Err> {
        Self::parse(input)
    }
}

impl fmt::Display for Nav {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ascii_grammar_is_strict() {
        for input in [
            "", ".1", "1.", "01", "00.1", "+1", "-0", " 1", "1 ", "1e2", "NaN", "Infinity", "１",
            "١", "1.2.3", "1\n", "1_000",
        ] {
            assert_eq!(Money::parse(input), Err(DecimalError::Format), "{input:?}");
        }
        for input in ["0", "1", "0.1", "123.456"] {
            assert!(Money::parse(input).is_ok());
        }
    }

    #[test]
    fn supplied_scale_and_positive_inputs() {
        assert_eq!(
            Shares::parse("1.0000000"),
            Err(DecimalError::Scale { maximum: 6 })
        );
        assert_eq!(
            Nav::parse("1.0000000"),
            Err(DecimalError::Scale { maximum: 6 })
        );
        assert_eq!(
            Money::parse("0.0000000000000"),
            Err(DecimalError::Scale { maximum: 12 })
        );
        assert_eq!(
            Shares::parse_positive("0.000000"),
            Err(DecimalError::NonPositive)
        );
        assert_eq!(Nav::parse("0"), Err(DecimalError::NonPositive));
        assert_eq!(Shares::parse("0").unwrap(), Shares::ZERO);
        assert_eq!(Money::parse("0").unwrap(), Money::ZERO);
    }

    #[test]
    fn fixed_output_is_exact_and_zero_unsigned() {
        assert_eq!(Money::parse("12.3").unwrap().to_string(), "12.300000000000");
        assert_eq!(Shares::parse("0.1").unwrap().to_string(), "0.100000");
        assert_eq!(Nav::parse("1.234567").unwrap().to_string(), "1.234567");
        assert_eq!(Money::ZERO.to_string(), "0.000000000000");
        assert_eq!(Shares::ZERO.to_string(), "0.000000");
        assert_eq!(
            format!("{:.2}", Money::parse("1.234").unwrap()),
            "1.234000000000"
        );
        assert_eq!(
            format!("{:.2}", Shares::parse("1.234").unwrap()),
            "1.234000"
        );
        assert_eq!(format!("{:.2}", Nav::parse("1.234").unwrap()), "1.234000");
    }

    #[test]
    fn fixed_scale_bounds_checked_before_construction() {
        assert_eq!(
            Money::parse("79228162514264337.593543950335")
                .unwrap()
                .coefficient(),
            MAX_COEFFICIENT
        );
        assert_eq!(
            Shares::parse("79228162514264337593543.950335")
                .unwrap()
                .coefficient(),
            MAX_COEFFICIENT
        );
        for input in [
            "79228162514264337.593543950336",
            "79228162514264338",
            "79228162514264337593543950335",
            "99999999999999999999999999999999999999999999999999999999",
        ] {
            assert_eq!(Money::parse(input), Err(DecimalError::Overflow));
        }
        assert_eq!(
            Shares::parse("79228162514264337593543.950336"),
            Err(DecimalError::Overflow)
        );
    }

    #[test]
    fn products_never_rescale_or_round() {
        assert_eq!(
            Shares::parse("0.123456")
                .unwrap()
                .checked_value(Nav::parse("1.234567").unwrap())
                .unwrap()
                .to_string(),
            "0.152414703552"
        );
        let max = Shares::parse("79228162514264337593543.950335").unwrap();
        assert_eq!(
            max.checked_value(Nav::parse("0.000001").unwrap())
                .unwrap()
                .coefficient(),
            MAX_COEFFICIENT
        );
        assert_eq!(
            max.checked_value(Nav::parse("0.000002").unwrap()),
            Err(DecimalError::Overflow)
        );
        assert_eq!(
            max.checked_value(Nav::parse("79228162514264337593543.950335").unwrap()),
            Err(DecimalError::Overflow)
        );
        assert_eq!(
            Shares::ZERO
                .checked_value(Nav::parse("1").unwrap())
                .unwrap(),
            Money::ZERO
        );
    }

    #[test]
    fn exact_sums_and_subtraction_boundaries() {
        let max = Money::parse("79228162514264337.593543950335").unwrap();
        let tick = Money::parse("0.000000000001").unwrap();
        assert_eq!(max.checked_add(tick), Err(DecimalError::Overflow));
        assert_eq!(
            max.checked_sub(tick).unwrap().checked_add(tick).unwrap(),
            max
        );
        assert_eq!(
            tick.checked_sub(tick).unwrap().to_string(),
            "0.000000000000"
        );
        assert_eq!(
            Money::ZERO.checked_sub(tick),
            Err(DecimalError::NegativeResult)
        );
        let max = Shares::parse("79228162514264337593543.950335").unwrap();
        let tick = Shares::parse("0.000001").unwrap();
        assert_eq!(max.checked_add(tick), Err(DecimalError::Overflow));
        assert_eq!(
            max.checked_sub(tick).unwrap().checked_add(tick).unwrap(),
            max
        );
    }
}
