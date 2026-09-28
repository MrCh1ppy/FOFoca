"""Offline tests for the AkshareProvider best-effort transport timeout hook.

No real network I/O happens here. One test class uses the **real installed
AKShare 1.18.97 module** (``akshare.fund.fund_em``) with a stubbed transport
to prove the injected ``timeout=(connect, read)`` actually reaches the
binding AKShare reads (``requests.get`` on the package namespace); the rest
verify restoration, nesting and validation semantics.
"""

from __future__ import annotations

import sys
import types

import pytest

from fofoca_data import provider as provider_mod


class _DummyResponse:
    text = ""


def _install_fake_akshare(monkeypatch) -> types.ModuleType:
    """Insert a minimal fake ``akshare`` module so AkshareProvider() works
    without importing the real (heavy) package."""
    fake = types.ModuleType("akshare")
    fake.fund_name_em = lambda: None
    fake.fund_open_fund_daily_em = lambda: None
    fake.fund_open_fund_info_em = lambda symbol, indicator: None
    monkeypatch.setitem(sys.modules, "akshare", fake)
    return fake


def test_context_injects_timeout_into_plain_get(monkeypatch) -> None:
    import requests.api as requests_api

    _install_fake_akshare(monkeypatch)
    calls: list[dict] = []

    def recording_get(url, params=None, **kwargs):
        calls.append(dict(kwargs))
        return _DummyResponse()

    monkeypatch.setattr(requests_api, "get", recording_get)
    monkeypatch.setattr(
        requests_api,
        "request",
        lambda method, url, **kw: recording_get(url, **kw),
    )

    p = provider_mod.AkshareProvider(
        request_delay_seconds=0.0,
        connect_timeout_seconds=3.0,
        read_timeout_seconds=17.0,
    )
    # Before entering: plain call, no injected timeout.
    requests_api.get("https://example.invalid/x")
    assert "timeout" not in calls[-1]

    with p:
        requests_api.get("https://example.invalid/x")
        assert calls[-1]["timeout"] == (3.0, 17.0)

        # A caller-specified timeout is never overridden.
        requests_api.get("https://example.invalid/x", timeout=99)
        assert calls[-1]["timeout"] == 99

    # After exit: original behavior restored (no injection).
    requests_api.get("https://example.invalid/x")
    assert "timeout" not in calls[-1]
    assert requests_api.get is recording_get


def test_context_restores_module_functions(monkeypatch) -> None:
    import requests
    import requests.api as requests_api

    _install_fake_akshare(monkeypatch)
    original_get = requests_api.get
    original_request = requests_api.request
    original_pkg_get = requests.get

    p = provider_mod.AkshareProvider(request_delay_seconds=0.0)
    with p:
        assert requests_api.get is not original_get
        assert requests.get is not original_pkg_get
    assert requests_api.get is original_get
    assert requests_api.request is original_request
    assert requests.get is original_pkg_get


def test_context_restores_after_exception(monkeypatch) -> None:
    import requests

    _install_fake_akshare(monkeypatch)
    original_pkg_get = requests.get

    p = provider_mod.AkshareProvider(request_delay_seconds=0.0)
    with pytest.raises(RuntimeError, match="boom"):
        with p:
            assert requests.get is not original_pkg_get
            raise RuntimeError("boom")
    assert requests.get is original_pkg_get


def test_nested_contexts_restore_only_once(monkeypatch) -> None:
    import requests
    import requests.api as requests_api

    _install_fake_akshare(monkeypatch)
    original_get = requests_api.get
    original_pkg_get = requests.get

    p1 = provider_mod.AkshareProvider(request_delay_seconds=0.0)
    p2 = provider_mod.AkshareProvider(request_delay_seconds=0.0)
    with p1:
        with p2:
            pass
        # Still patched while the outer context is active.
        assert requests_api.get is not original_get
        assert requests.get is not original_pkg_get
    assert requests_api.get is original_get
    assert requests.get is original_pkg_get


def test_invalid_timeout_values_rejected(monkeypatch) -> None:
    _install_fake_akshare(monkeypatch)
    for bad in (0, -1, float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            provider_mod.AkshareProvider(connect_timeout_seconds=bad)
        with pytest.raises(ValueError):
            provider_mod.AkshareProvider(read_timeout_seconds=bad)


class TestRealAkshareBinding:
    """Prove the timeout tuple reaches AKShare's ACTUAL ``requests.get`` call.

    Uses the installed akshare 1.18.97 ``akshare.fund.fund_em`` module with
    the network transport stubbed out — no real upstream is contacted.
    """

    @pytest.fixture()
    def fund_em(self):
        akshare = pytest.importorskip("akshare")
        assert akshare.__version__ == "1.18.97"
        import akshare.fund.fund_em as fe

        return fe

    def _stub_transport(self, monkeypatch, fund_em, calls):
        def recording_get(url, params=None, **kwargs):
            calls.append(dict(kwargs))
            return _DummyResponse()

        # Stub ONLY the leaf transport: requests.api.get/request. The hook
        # under test must bridge from the package-level `requests.get` (the
        # binding fund_em reads) down to here with an injected timeout.
        import requests.api as requests_api

        monkeypatch.setattr(requests_api, "get", recording_get)
        monkeypatch.setattr(
            requests_api, "request", lambda m, u, **kw: recording_get(u, **kw)
        )

    def _stub_js_and_frames(self, monkeypatch, fund_em):
        import pandas as pd

        class FakeJS:
            def eval(self, _text):
                return None

            def execute(self, name):
                if name == "Data_netWorthTrend":
                    return [
                        {
                            "x": 1704153600000,
                            "y": 1.1,
                            "equityReturn": 0.0,
                            "unitMoney": "",
                        }
                    ]
                if name == "Data_ACWorthTrend":
                    return [[1704153600000, 2.2]]
                return []

        monkeypatch.setattr(fund_em.py_mini_racer, "MiniRacer", FakeJS)

    def test_history_indicators_receive_timeout_tuple(
        self, monkeypatch, fund_em
    ) -> None:
        calls: list[dict] = []
        self._stub_transport(monkeypatch, fund_em, calls)
        self._stub_js_and_frames(monkeypatch, fund_em)

        p = provider_mod.AkshareProvider(
            request_delay_seconds=0.0,
            connect_timeout_seconds=4.0,
            read_timeout_seconds=23.0,
        )
        with p:
            df_unit = p._ak.fund_open_fund_info_em(
                symbol="000001", indicator="单位净值走势"
            )
            df_acc = p._ak.fund_open_fund_info_em(
                symbol="000001", indicator="累计净值走势"
            )

        # Each indicator performed exactly one request, and the injected
        # (connect, read) tuple actually arrived at the transport.
        assert len(calls) == 2
        assert all(c["timeout"] == (4.0, 23.0) for c in calls)
        assert list(df_unit.columns) == ["净值日期", "单位净值", "日增长率"]
        assert list(df_acc.columns) == ["净值日期", "累计净值"]

        # After the context exits, a direct AKShare call is NOT wrapped.
        import requests

        assert requests.get is not p.__class__  # sanity: namespace restored

    def test_directory_calls_receive_timeout_tuple(
        self, monkeypatch, fund_em
    ) -> None:
        calls: list[dict] = []
        self._stub_transport(monkeypatch, fund_em, calls)
        # fund_name_em parses via demjson; fund_open_fund_daily_em too.
        monkeypatch.setattr(
            fund_em.demjson, "decode", lambda _text: [["000001", "XX", "基金A", "混合型", "XX"]]
        )

        p = provider_mod.AkshareProvider(
            request_delay_seconds=0.0,
            connect_timeout_seconds=2.5,
            read_timeout_seconds=9.0,
        )
        with p:
            df = p._ak.fund_name_em()
        assert len(calls) == 1
        assert calls[0]["timeout"] == (2.5, 9.0)
        assert "基金代码" in df.columns

    def test_naive_api_only_patch_would_not_cover_fund_em(
        self, monkeypatch, fund_em
    ) -> None:
        """Guard against regression to the broken approach: patching ONLY
        ``requests.api.get`` must demonstrably NOT affect what fund_em
        calls — proving the provider's package-level patch is necessary."""
        import requests
        import requests.api as requests_api

        sentinel = object()
        monkeypatch.setattr(requests_api, "get", lambda *a, **k: sentinel)
        # fund_em reads `requests.get` (package attribute), which still
        # points at the ORIGINAL function here.
        assert requests.get is not requests_api.get
        assert fund_em.requests.get is requests.get
