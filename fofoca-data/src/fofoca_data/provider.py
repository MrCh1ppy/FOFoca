"""Thin wrapper around AKShare 1.18.97.

All upstream access goes through this module so tests can substitute
deterministic fixtures. Functions return raw ``pandas.DataFrame`` objects
exactly as AKShare yields them; normalization lives elsewhere.

Rate limiting is a simple, sequential sleep between provider calls. We do not
parallelize requests because (a) EastMoney throttles aggressively, and (b) the
target host has limited RAM. See ``README.md``.

Transport timeout notes (inspected against the installed AKShare 1.18.97):

  * ``fund_open_fund_info_em(symbol, "单位净值走势" | "累计净值走势")`` in
    ``akshare/fund/fund_em.py`` performs exactly ONE plain
    ``requests.get(url, headers=headers)`` per call against
    ``fund.eastmoney.com/pingzhongdata/{symbol}.js`` — **no timeout
    parameter** (so the default is an unbounded blocking socket), no retries,
    no ``Session``. Both history indicators we use share this single request
    (they read different variables from the same JS payload), so one patched
    transport covers both.
  * ``fund_name_em`` and ``fund_open_fund_daily_em`` (used at selection time)
    follow the same pattern: a single plain ``requests.get(...)`` with no
    timeout, no retries, no session.

Binding-path subtlety (why a naive patch does NOT work): AKShare does
``import requests`` at module top and calls ``requests.get(...)``, which
reads the attribute from the ``requests`` **package** namespace.
``requests/__init__.py`` itself does ``from .api import get, ...``, so
patching only ``requests.api.get`` replaces a binding AKShare never reads
— the timeout would silently never apply. The provider therefore patches,
via :func:`unittest.mock.patch.object` (guaranteed restoration on exit):

  1. ``requests.get`` / ``requests.request`` (the binding AKShare reads);
  2. ``requests.api.get`` / ``requests.api.request`` (in case any AKShare
     module calls ``requests.api.*`` directly);
  3. the ``requests`` module object referenced by every already-imported
     ``akshare.*`` module (defensive: if AKShare ever did
     ``from requests import get``, its module-global alias is repointed to
     the package attribute, which is itself patched).

The wrapper injects finite ``timeout=(connect, read)`` values whenever the
caller did not specify any timeout. This is a **best-effort** bound:

  * it covers every AKShare HTTP call on this single worker, with no extra
    threads and no overlapping fetches — the calling thread itself receives
    ``requests.exceptions.ConnectTimeout``/``ReadTimeout``;
  * DNS resolution honors the connect timeout only insofar as
    urllib3/socket applies it; pure-Python work after the response (e.g.
    ``py_mini_racer`` JS evaluation of a huge payload) is **not** bounded;
  * it is not a hard wall-clock cancellation of arbitrary blocked library
    calls. The hard bound for a stalled run is an external, manually
    controlled process limit (see ``deploy/README.md``), and an abrupt kill
    may preclude the final report.

The patches are reference-counted so nested provider contexts stay safe, are
always undone on exit (including on exception), and are never applied to
SQL work.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Protocol
from unittest import mock

import pandas as pd

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_READ_TIMEOUT_SECONDS = 60.0

_patch_lock = threading.Lock()
_patch_depth = 0
_active_patchers: list[mock._patch] = []


def _validate_timeout(name: str, value: float) -> float:
    """Return ``value`` as a finite positive float or raise ``ValueError``.

    NaN and infinities are rejected: a NaN timeout would silently disable the
    bound (comparisons against NaN are always false), and an infinite one is
    equivalent to no bound at all.
    """
    import math

    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(v) or v <= 0:
        raise ValueError(f"{name} must be a finite number > 0, got {value!r}")
    return v


def _make_timeout_wrappers(original, timeout_pair):  # type: ignore[no-untyped-def]
    """Build (request_wrapper, get_wrapper) injecting ``timeout_pair`` when unset."""

    def request_with_timeout(method, url, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = timeout_pair
        return original["request"](method, url, **kwargs)

    def get_with_timeout(url, params=None, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = timeout_pair
        return original["get"](url, params=params, **kwargs)

    return request_with_timeout, get_with_timeout


def _patch_requests_timeouts(connect: float, read: float) -> None:
    """Patch every binding path AKShare can read, restorably.

    Callers that pass their own ``timeout=`` are never overridden, so any
    future AKShare code that sets its own timeout keeps its own policy.
    """
    import requests
    import requests.api as requests_api  # local import: tests need no network

    global _active_patchers
    originals = {"request": requests_api.request, "get": requests_api.get}
    timeout_pair = (float(connect), float(read))
    request_wrapper, get_wrapper = _make_timeout_wrappers(originals, timeout_pair)

    patchers: list[mock._patch] = [
        # 1. The binding AKShare actually reads: attributes of the package.
        mock.patch.object(requests, "get", get_wrapper),
        mock.patch.object(requests, "request", request_wrapper),
        # 2. The canonical location, in case anything calls requests.api.*
        #    directly (requests.get resolves here at call time anyway).
        mock.patch.object(requests_api, "get", get_wrapper),
        mock.patch.object(requests_api, "request", request_wrapper),
    ]
    # 3. Defensive: if any already-imported akshare module holds its own
    #    `requests` reference (or a `from requests import get` alias), make
    #    sure that reference is the (now patched) package namespace.
    for module in list(sys.modules.values()):
        mod_name = getattr(module, "__name__", "") or ""
        if not (mod_name == "akshare" or mod_name.startswith("akshare.")):
            continue
        mod_requests = getattr(module, "requests", None)
        if mod_requests is not None and mod_requests is not requests:
            patchers.append(mock.patch.object(module, "requests", requests))
        mod_get = getattr(module, "get", None)
        if mod_get is originals["get"] or mod_get is requests_api.get:
            patchers.append(mock.patch.object(module, "get", get_wrapper))

    for patcher in patchers:
        patcher.start()
    _active_patchers = patchers


def _unpatch_requests_timeouts() -> None:
    global _active_patchers
    for patcher in reversed(_active_patchers):
        patcher.stop()
    _active_patchers = []


class Provider(Protocol):
    """Protocol implemented by the live AKShare-backed provider and by fixtures."""

    def fund_name_em(self) -> pd.DataFrame: ...

    def fund_open_fund_daily_em(self) -> pd.DataFrame: ...

    def fund_open_fund_info_em(self, symbol: str, indicator: str) -> pd.DataFrame: ...


class AkshareProvider:
    """Live provider backed by ``akshare`` with a fixed inter-request delay.

    Parameters:
        request_delay_seconds: fixed sleep between consecutive upstream calls.
        connect_timeout_seconds: best-effort TCP connect bound per request.
        read_timeout_seconds: best-effort socket read bound per request.

    Use as a context manager to activate the best-effort transport timeouts::

        with AkshareProvider() as provider:
            report = run_backfill(conn, provider, ...)

    Timeouts are documented as best-effort, never as hard cancellation.
    """

    def __init__(
        self,
        request_delay_seconds: float = 1.0,
        *,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
    ) -> None:
        if request_delay_seconds < 0:
            raise ValueError("request_delay_seconds must be >= 0")
        self._delay = float(request_delay_seconds)
        self._connect_timeout = _validate_timeout(
            "connect_timeout_seconds", connect_timeout_seconds
        )
        self._read_timeout = _validate_timeout(
            "read_timeout_seconds", read_timeout_seconds
        )
        import akshare as ak  # imported lazily so tests need not import akshare

        self._ak = ak

    # -- best-effort timeout context ------------------------------------

    def __enter__(self) -> "AkshareProvider":
        global _patch_depth
        with _patch_lock:
            if _patch_depth == 0:
                _patch_requests_timeouts(self._connect_timeout, self._read_timeout)
            _patch_depth += 1
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        global _patch_depth
        with _patch_lock:
            _patch_depth -= 1
            if _patch_depth <= 0:
                _patch_depth = 0
                _unpatch_requests_timeouts()

    # -- provider surface ------------------------------------------------

    def _sleep(self) -> None:
        if self._delay > 0:
            time.sleep(self._delay)

    def fund_name_em(self) -> pd.DataFrame:
        self._sleep()
        return self._ak.fund_name_em()

    def fund_open_fund_daily_em(self) -> pd.DataFrame:
        self._sleep()
        return self._ak.fund_open_fund_daily_em()

    def fund_open_fund_info_em(self, symbol: str, indicator: str) -> pd.DataFrame:
        self._sleep()
        return self._ak.fund_open_fund_info_em(symbol=symbol, indicator=indicator)


__all__ = [
    "Provider",
    "AkshareProvider",
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_READ_TIMEOUT_SECONDS",
]
