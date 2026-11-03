"""Explicit API configuration and immutable exact NAV history; no engine mutation."""
from dataclasses import dataclass
from datetime import date, datetime
from . import _native
from ._native import Config, ConfigurationError, InputError, TransportError, HttpError, ResponseError


@dataclass(frozen=True)
class NavRow:
    nav_date: date
    unit_nav: str | None
    accumulated_nav: str | None


@dataclass(frozen=True)
class History:
    code: str
    start: date
    end: date
    rows: tuple[NavRow, ...]


def fetch_nav_history(code: str, start: date, end: date, *, config: Config) -> History:
    """Fetch only stored rows in the inclusive range; failures return no history."""
    if not isinstance(start, date) or not isinstance(end, date) or isinstance(start, datetime) or isinstance(end, datetime):
        raise InputError("Expected datetime.date endpoints")
    value = _native.fetch_nav_history(code, start.isoformat(), end.isoformat(), config=config)
    return History(value["code"], date.fromisoformat(value["start"]), date.fromisoformat(value["end"]),
                   tuple(NavRow(date.fromisoformat(r["nav_date"]), r["unit_nav"], r["accumulated_nav"])
                         for r in value["rows"]))
