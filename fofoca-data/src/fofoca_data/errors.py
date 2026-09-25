"""Shared exceptions for fofoca-data."""

from __future__ import annotations


class FofocaError(Exception):
    """Base class for fofoca-data errors."""


class InputValidationError(FofocaError):
    """Invalid user-supplied input (bad code, bad date, reversed range)."""


class SelectionError(FofocaError):
    """Fund selection failed closed (directory fetch/parse failure)."""


class ProviderError(FofocaError):
    """AKShare / upstream request failed."""


class NavDataError(FofocaError):
    """Provider returned malformed or unusable NAV payload for a fund."""


class DatabaseError(FofocaError):
    """The database itself is unreachable or unusable (fatal to the run)."""
