"""Unit tests for ``redact.sanitize_error``."""

from __future__ import annotations

import os
from unittest.mock import patch

from fofoca_data.redact import sanitize_error


class TestSanitizeError:
    def test_redacts_url_userinfo(self) -> None:
        msg = "could not connect: postgresql://alice:secretP4ss@db.example.com:5432/fofoca"
        out = sanitize_error(msg)
        assert "secretP4ss" not in out
        assert "alice" not in out.split("@")[0] or "***" in out
        assert "db.example.com" in out

    def test_redacts_password_kwarg(self) -> None:
        msg = "connection failed: host=db user=alice password=hunter2 port=5432"
        out = sanitize_error(msg)
        assert "hunter2" not in out
        assert "password=***" in out

    def test_redacts_password_case_insensitive(self) -> None:
        msg = "Password=foo123 PASSWD=bar456 Pwd=baz789"
        out = sanitize_error(msg)
        for s in ("foo123", "bar456", "baz789"):
            assert s not in out

    def test_redacts_env_var_literal(self) -> None:
        dsn = "postgresql://alice:envSecret123@db.internal:5432/fofoca"
        with patch.dict(os.environ, {"FOFOCA_DATABASE_URL": dsn}):
            msg = f"failed to connect using {dsn}"
            out = sanitize_error(msg)
            assert "envSecret123" not in out
            assert "<FOFOCA_DATABASE_URL>" in out

    def test_env_var_not_set_no_crash(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("FOFOCA_DATABASE_URL", None)
            out = sanitize_error("plain error with password=foo")
            assert "foo" not in out

    def test_truncates_long_messages(self) -> None:
        long_msg = "x" * 10000
        out = sanitize_error(long_msg, max_len=100)
        assert len(out) <= 100

    def test_non_string_input(self) -> None:
        class Weird:
            def __str__(self) -> str:
                return "weird password=abc"

        out = sanitize_error(Weird())
        assert "abc" not in out
        assert "weird" in out
