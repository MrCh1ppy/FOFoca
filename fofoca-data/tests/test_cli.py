"""Offline tests for the CLI surface (argument validation, exit codes)."""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from fofoca_data import cli
from fofoca_data.db import NavWrite, apply_schema, record_fund_success
from fofoca_data.eligibility import FundCandidate


class TestInputValidation:
    def test_bad_code_rejected(self, capsys) -> None:
        rc = cli.main(["query", "--code", "12345", "--start", "2024-01-01", "--end", "2024-01-31"])
        assert rc == cli.EXIT_USAGE
        err = capsys.readouterr().err
        assert "six digits" in err

    def test_bad_date_rejected(self, capsys) -> None:
        rc = cli.main(["query", "--code", "000001", "--start", "2024-13-01", "--end", "2024-12-31"])
        assert rc == cli.EXIT_USAGE

    def test_reversed_range_rejected(self, capsys) -> None:
        rc = cli.main(["query", "--code", "000001", "--start", "2024-12-31", "--end", "2024-01-01"])
        assert rc == cli.EXIT_USAGE
        err = capsys.readouterr().err
        assert "after" in err

    def test_missing_db_url_rejected(self, capsys, monkeypatch) -> None:
        monkeypatch.delenv("FOFOCA_DATABASE_URL", raising=False)
        rc = cli.main(["query", "--code", "000001", "--start", "2024-01-01", "--end", "2024-01-31"])
        assert rc == cli.EXIT_USAGE
        err = capsys.readouterr().err
        assert "FOFOCA_DATABASE_URL" in err

    def test_backfill_bad_code_rejected(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("FOFOCA_DATABASE_URL", "postgresql://unused")
        rc = cli.main(["backfill", "--code", "abc"])
        assert rc == cli.EXIT_USAGE

    def test_backfill_invalid_target_date_rejected_before_db(self, capsys, monkeypatch) -> None:
        """An invalid --target-date fails input validation without touching
        the DB or the network (exit 2, usage error)."""
        monkeypatch.setenv("FOFOCA_DATABASE_URL", "postgresql://unused")
        rc = cli.main(["backfill", "--code", "000001", "--target-date", "2026-13-40"])
        assert rc == cli.EXIT_USAGE
        err = capsys.readouterr().err
        assert "calendar date" in err

    def test_backfill_target_date_non_date_rejected(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("FOFOCA_DATABASE_URL", "postgresql://unused")
        rc = cli.main(["backfill", "--target-date", "not-a-date"])
        assert rc == cli.EXIT_USAGE

    def test_db_connection_failure_does_not_leak_dsn(self, capsys, monkeypatch) -> None:
        """A bad connection string must produce an error message that does
        NOT echo the DSN (or its password) back to stderr."""
        dsn = "postgresql://fofoca_app:topS3cret@127.0.0.1:59999/fofoca"
        monkeypatch.setenv("FOFOCA_DATABASE_URL", dsn)
        rc = cli.main(
            ["query", "--code", "000001", "--start", "2024-01-01", "--end", "2024-01-31"]
        )
        assert rc != cli.EXIT_OK
        err = capsys.readouterr().err
        assert "topS3cret" not in err
        assert "fofoca_app:topS3cret" not in err
        # And the literal DSN string itself is gone (it's replaced by the
        # FOFOCA_DATABASE_URL placeholder).
        assert dsn not in err

    def test_bad_timeout_env_rejected(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("FOFOCA_DATABASE_URL", "postgresql://unused")
        monkeypatch.setenv("FOFOCA_READ_TIMEOUT_SECONDS", "-5")
        rc = cli.main(["backfill", "--code", "000001"])
        assert rc == cli.EXIT_USAGE
        err = capsys.readouterr().err
        assert "FOFOCA_READ_TIMEOUT_SECONDS" in err

    @pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "NaN", "Infinity"])
    def test_non_finite_timeout_env_rejected(self, capsys, monkeypatch, bad) -> None:
        """NaN/inf timeout env values must be rejected (a NaN timeout would
        silently disable the bound; an infinite one is no bound at all)."""
        monkeypatch.setenv("FOFOCA_DATABASE_URL", "postgresql://unused")
        monkeypatch.setenv("FOFOCA_CONNECT_TIMEOUT_SECONDS", bad)
        rc = cli.main(["backfill", "--code", "000001"])
        assert rc == cli.EXIT_USAGE
        err = capsys.readouterr().err
        assert "FOFOCA_CONNECT_TIMEOUT_SECONDS" in err


@pytest.mark.skipif(
    os.environ.get("FOFOCA_INTEGRATION_DB") != "true"
    or not os.environ.get("FOFOCA_TEST_DATABASE_URL"),
    reason="disposable DB integration disabled",
)
class TestQueryAgainstDisposableDB:
    @pytest.fixture()
    def db_url(self):
        url = os.environ["FOFOCA_TEST_DATABASE_URL"]
        with psycopg.connect(url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DROP TABLE IF EXISTS fund_sync_state, fund_nav_daily, fund CASCADE"
                )
            apply_schema(conn)
            record_fund_success(
                conn,
                FundCandidate(code="000001", name="测试基金", fund_type="混合型-灵活"),
                [
                    NavWrite(
                        nav_date=date(2024, 1, 5),
                        historical_unit_nav=Decimal("1.100000"),
                    ),
                    NavWrite(
                        nav_date=date(2024, 1, 10),
                        historical_accumulated_nav=Decimal("2.500000"),
                    ),
                ],
            )
        return url

    def test_query_returns_nullable_decimal_strings(self, db_url, capsys, monkeypatch) -> None:
        monkeypatch.setenv("FOFOCA_DATABASE_URL", db_url)
        rc = cli.main(
            ["query", "--code", "000001", "--start", "2024-01-01", "--end", "2024-01-31"]
        )
        assert rc == cli.EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["code"] == "000001"
        assert [r["nav_date"] for r in payload["rows"]] == ["2024-01-05", "2024-01-10"]
        assert payload["rows"][0]["unit_nav"] == "1.100000"
        assert payload["rows"][0]["accumulated_nav"] is None
        assert payload["rows"][1]["unit_nav"] is None
        assert payload["rows"][1]["accumulated_nav"] == "2.500000"

    def test_query_unknown_code_empty(self, db_url, capsys, monkeypatch) -> None:
        monkeypatch.setenv("FOFOCA_DATABASE_URL", db_url)
        rc = cli.main(
            ["query", "--code", "999999", "--start", "2024-01-01", "--end", "2024-01-31"]
        )
        assert rc == cli.EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["rows"] == []


@pytest.mark.skipif(
    os.environ.get("FOFOCA_INTEGRATION_DB") != "true"
    or not os.environ.get("FOFOCA_TEST_DATABASE_URL"),
    reason="disposable DB integration disabled",
)
class TestInitDbCliAgainstDisposableDB:
    @pytest.fixture()
    def db_url(self):
        url = os.environ["FOFOCA_TEST_DATABASE_URL"]
        with psycopg.connect(url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DROP TABLE IF EXISTS fund_sync_state, fund_nav_daily, fund CASCADE"
                )
        return url

    def test_init_db_creates_two_tables_and_is_repeatable(
        self, db_url, capsys, monkeypatch
    ) -> None:
        monkeypatch.setenv("FOFOCA_DATABASE_URL", db_url)
        for _ in range(2):
            rc = cli.main(["init-db"])
            assert rc == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "fund, fund_nav_daily" in out
        with psycopg.connect(db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('fund'), to_regclass('fund_nav_daily'), to_regclass('fund_sync_state')")
            fund, nav, state = cur.fetchone()
        assert fund is not None and nav is not None
        assert state is None  # never (re)created by init-db
