"""Integration test proving the target-date coverage lookup transaction is
fully finished (not idle-in-transaction) before any provider network call.

This is the core "psycopg read transaction ends before network I/O"
requirement, verified against a disposable PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from fofoca_data.backfill import run_backfill
from fofoca_data.db import NavWrite, apply_schema, record_fund_success
from fofoca_data.eligibility import FundCandidate

from .fixtures import (
    FixtureProvider,
    make_accumulated_nav_df,
    make_daily_df,
    make_name_df,
    make_unit_nav_df,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("FOFOCA_INTEGRATION_DB") != "true"
    or not os.environ.get("FOFOCA_TEST_DATABASE_URL"),
    reason="disposable DB integration disabled",
)

TARGET = date(2026, 9, 24)


class TxStateProbeError(RuntimeError):
    pass


class TxStateProbingProvider(FixtureProvider):
    """Fails loudly if the psycopg connection is still inside a transaction
    (or in a failed transaction) at the moment a provider call happens."""

    def __init__(self, conn: psycopg.Connection, **kwargs) -> None:
        super().__init__(**kwargs)
        self._conn = conn
        self.states: list[str] = []

    def _check(self, label: str) -> None:
        status = self._conn.info.transaction_status
        self.states.append(f"{label}:{status}")
        # psycopg.pq.TransactionStatus: IDLE == 0 means no open transaction.
        if int(status) != 0:
            raise TxStateProbeError(
                f"DB transaction still open during {label}: status={status}"
            )

    def fund_open_fund_info_em(self, symbol: str, indicator: str):
        self._check(f"fetch:{symbol}:{indicator}")
        return super().fund_open_fund_info_em(symbol, indicator)


@pytest.fixture()
def conn():
    url = os.environ["FOFOCA_TEST_DATABASE_URL"]
    # The backfill orchestrator is driven with autocommit=False (as in the
    # CLI), so "IDLE between funds" really means "no open transaction".
    with psycopg.connect(url, autocommit=False) as c:
        with c.cursor() as cur:
            cur.execute(
                "DROP TABLE IF EXISTS fund_sync_state, fund_nav_daily, fund CASCADE"
            )
        apply_schema(c)
        yield c


def _provider_kwargs() -> dict:
    return dict(
        name_df=make_name_df(
            [("000001", "基金A", "混合型-灵活"), ("000003", "基金C", "混合型-灵活")]
        ),
        daily_df=make_daily_df(
            [("000001", "开放申购", "开放赎回"), ("000003", "开放申购", "开放赎回")],
            dated=[("000001", "1.5", "2.5"), ("000003", "3.5", "4.5")],
        ),
        unit_nav={
            "000001": make_unit_nav_df([("2024-01-02", 1.1)]),
            "000003": make_unit_nav_df([("2024-02-01", 3.1)]),
        },
        accumulated_nav={
            "000001": make_accumulated_nav_df([("2024-01-02", 2.1)]),
            "000003": make_accumulated_nav_df([("2024-02-01", 4.1)]),
        },
    )


class TestReadTransactionEndsBeforeNetworkIO:
    def test_no_open_transaction_during_fetches(self, conn) -> None:
        # Seed 000001 already at target -> it will be SKIPPED without fetch;
        # 000003 will be attempted (its stored max is below T).
        record_fund_success(
            conn,
            FundCandidate(code="000001", name="X", fund_type="混合型"),
            [NavWrite(nav_date=TARGET, historical_unit_nav=Decimal("1.234567"))],
        )
        provider = TxStateProbingProvider(conn, **_provider_kwargs())

        report = run_backfill(
            conn, provider, supplied_codes=None, target_date=TARGET
        )

        by_code = {r.candidate.code: r for r in report.fund_results}
        assert by_code["000001"].status == "SKIPPED"
        assert by_code["000003"].status == "SUCCESS"
        # Both indicator fetches for 000003 happened with an IDLE connection.
        assert provider.states == [
            "fetch:000003:单位净值走势:0",
            "fetch:000003:累计净值走势:0",
        ]
        # And after the whole run the connection is not left in a transaction.
        assert int(conn.info.transaction_status) == 0

    def test_no_open_transaction_when_lookup_must_probe_every_fund(self, conn) -> None:
        # Nothing seeded: every selected fund is attempted; every fetch must
        # still see an IDLE connection (the batch lookup committed up front).
        provider = TxStateProbingProvider(conn, **_provider_kwargs())
        report = run_backfill(
            conn, provider, supplied_codes=None, target_date=TARGET
        )
        assert report.failed == 0
        assert len(provider.states) == 4  # 2 funds x 2 indicators
        assert all(s.endswith(":0") for s in provider.states)
