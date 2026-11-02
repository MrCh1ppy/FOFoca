"""Run using an isolated interpreter with the real wheel installed, not PYTHONPATH."""
import importlib.resources
import os
from pathlib import Path
import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType


def no_child_process(event, args):
    if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.fork", "os.exec"}:
        raise AssertionError(f"Runtime must not start a child process: {event}")


# Prohibit real child-process operations before importing the installed package.
sys.addaudithook(no_child_process)
os.environ["PATH"] = ""

import fofoca_engine
from fofoca_engine import (AlreadyInitializedError, BacktestSession, FinishedError,
                           MarketConflictError, NotInitializedError)
from fofoca_engine import _native


class WheelContract(unittest.TestCase):
    def test_installed_native_and_typing(self):
        assert fofoca_engine.__file__ is not None
        self.assertIn("site-packages", str(Path(fofoca_engine.__file__).resolve()))
        self.assertTrue(_native.__file__.endswith((".so", ".pyd")))
        resources = importlib.resources.files(fofoca_engine)
        self.assertTrue(resources.joinpath("py.typed").is_file())
        self.assertTrue(resources.joinpath("_native.pyi").is_file())

    def test_independent_native_objects(self):
        first, second = BacktestSession(), BacktestSession()
        self.assertIsNot(first._session, second._session)
        for session in (first, second, first):
            with self.assertRaises(NotInitializedError):
                session.account()
        self.assertFalse(hasattr(first, "__dict__"))

    def test_module_exception(self):
        for error in (NotInitializedError, AlreadyInitializedError, FinishedError, MarketConflictError):
            self.assertIs(error, getattr(_native, error.__name__))
            self.assertEqual(error.__module__, "fofoca_engine._native")
            self.assertTrue(issubclass(error, RuntimeError))

    def test_repeat_initialize_preserves_state(self):
        session = BacktestSession()
        session.initialize(date(2026, 1, 1), date(2026, 1, 2), "100")
        before = session.account()
        for _ in range(2):
            with self.assertRaises(AlreadyInitializedError):
                session.initialize(date(2027, 1, 1), date(2027, 1, 2), "200")
            self.assertEqual(session.account(), before)

    def test_all_business_rejected(self):
        session = BacktestSession()
        for method, args in (
            (session.account, ()),
            (session.advance_day, ([], {})),
            (session.report, ()),
        ):
            with self.subTest(method=method.__name__):
                with self.assertRaises(NotInitializedError):
                    method(*args)

    def test_initialize_type_errors(self):
        session = BacktestSession()
        today = date(2026, 1, 1)
        for args in (("2026-01-01", today, "100"), (today, None, "100"),
                     (today, today, 100.0), (today, today, 100),
                     (today, today, Decimal("100")), (today, today, None),
                     (datetime(2026, 1, 1), today, "100"),
                     (today, datetime(2026, 1, 1, 12), "100")):
            with self.subTest(args=args), self.assertRaises(TypeError):
                session.initialize(*args)
        with self.assertRaises(TypeError):
            session.initialize()

    def test_container_type_errors(self):
        session = BacktestSession()
        for args in (({}, {}), ((), {}), ([], []), ([], None), ({},), ()):
            with self.subTest(args=args), self.assertRaises(TypeError):
                session.advance_day(*args)
        self.assertFalse(hasattr(session, "set_orders"))
        self.assertFalse(hasattr(session._session, "set_orders"))

    def test_direct_native_boundary(self):
        session = _native.Session()
        with self.assertRaises(_native.NotInitializedError):
            session.account()
        with self.assertRaises(TypeError):
            session.initialize(datetime(2026, 1, 1), date(2026, 1, 2), "100")
        session.initialize(date(2026, 1, 1), date(2026, 1, 2), "100")
        self.assertEqual(session.account()["account"]["cash"], "100.000000000000")
        with self.assertRaises(NotImplementedError):
            session.report()

    def test_cash_grammar_and_scale_errors_recover(self):
        today = date(2026, 1, 1)
        for cash in ("-1", "-0", "+1", " 1", "1 ", "NaN", "Infinity", "1e3", "01",
                     ".1", "1.", "１", "١", "1.0000000000000", "0.0000000000001"):
            with self.subTest(cash=cash):
                session = BacktestSession()
                with self.assertRaises(ValueError):
                    session.initialize(today, today, cash)
                with self.assertRaises(NotInitializedError):
                    session.account()
                session.initialize(today, today, "0.1")
                self.assertEqual(session.account()["account"]["cash"], "0.100000000000")

    def test_date_range_failure_then_valid(self):
        session = BacktestSession()
        with self.assertRaises(ValueError):
            session.initialize(date(2026, 1, 2), date(2026, 1, 1), "100")
        with self.assertRaises(NotInitializedError):
            session.account()
        session.initialize(date(2026, 1, 1), date(2026, 1, 1), "100")
        self.assertEqual(session.account()["start_date"], session.account()["end_date"])

    def test_cash_boundary_and_overflow_recovery(self):
        today = date(2026, 1, 1)
        for cash in ("79228162514264337.593543950336", "79228162514264338",
                     "79228162514264337593543950335", "9" * 100):
            with self.subTest(cash=cash):
                session = BacktestSession()
                with self.assertRaises(OverflowError):
                    session.initialize(today, today, cash)
                with self.assertRaises(NotInitializedError):
                    session.account()
                session.initialize(today, today, "79228162514264337.593543950335")
                self.assertEqual(session.account()["account"]["total_assets"],
                                 "79228162514264337.593543950335")

    def test_zero_cash_and_calendar_bounds(self):
        for today in (date.min, date.max):
            session = BacktestSession()
            session.initialize(today, today, "0.000000000000")
            snapshot = session.account()
            self.assertEqual(snapshot["current_date"], today)
            self.assertEqual(snapshot["account"]["cash"], "0.000000000000")
            self.assertEqual(snapshot["account"]["total_assets"], "0.000000000000")

    def test_exact_account_shape_and_dates(self):
        session = BacktestSession()
        start, end = date(2026, 1, 1), date(2026, 1, 3)
        session.initialize(start, end, "100.123456789012")
        expected = {
            "status": "running", "start_date": start, "end_date": end,
            "current_date": start, "last_closed_date": None,
            "snapshot_pinned": False, "orders": [],
            "account": {
                "valuation_date": start, "cash": "100.123456789012",
                "holdings": [], "pending_buys": [], "pending_cash": [],
                "confirmed_value": "0.000000000000", "pending_buy_value": "0.000000000000",
                "pending_cash_value": "0.000000000000", "total_assets": "100.123456789012",
            },
        }
        self.assertEqual(session.account(), expected)
        snapshot = session.account()
        for field in ("start_date", "end_date", "current_date"):
            self.assertIs(type(snapshot[field]), date)
        self.assertIs(type(snapshot["account"]["valuation_date"]), date)
        self.assertIs(snapshot["snapshot_pinned"], False)
        self.assertEqual(set(fofoca_engine.Snapshot.__annotations__), set(expected))
        self.assertEqual(set(fofoca_engine.Account.__annotations__), set(expected["account"]))

    def test_nested_results_are_detached_and_sessions_independent(self):
        first, second = BacktestSession(), BacktestSession()
        today = date(2026, 1, 1)
        first.initialize(today, today, "100")
        second.initialize(today, today, "200")
        before = first.account()
        changed = first.account()
        self.assertIsNot(changed, before)
        self.assertIsNot(changed["account"], before["account"])
        for field in ("holdings", "pending_buys", "pending_cash"):
            self.assertIsNot(changed["account"][field], before["account"][field])
            changed["account"][field].append({"invented": []})
        changed["orders"].append({"code": "000001", "side": "buy", "shares": "1"})
        changed["account"]["cash"] = "0"
        changed["current_date"] = date.max
        self.assertEqual(first.account(), before)
        self.assertEqual(second.account()["account"]["cash"], "200.000000000000")

    def test_report_still_unimplemented(self):
        session = BacktestSession()
        today = date(2026, 1, 1)
        session.initialize(today, today, "100")
        before = session.account()
        with self.assertRaises(NotImplementedError):
            session.report()
        self.assertEqual(session.account(), before)

    def session(self, cash="100", start=date(2026, 1, 1), end=date(2026, 1, 4)):
        session = BacktestSession()
        session.initialize(start, end, cash)
        return session

    def order(self, shares="1", side="buy", code="000001"):
        return {"code": code, "side": side, "shares": shares}

    def test_four_day_buy_arrive_sell_arrive(self):
        session = self.session()
        first = session.advance_day([self.order("0.123456")], {"000001": "1.234567"})
        self.assertEqual(set(first), set(fofoca_engine.Advanced.__annotations__))
        self.assertEqual(first["closed_date"], date(2026, 1, 1))
        self.assertEqual(first["executions"], [{
            "code": "000001", "side": "buy", "shares": "0.123456",
            "unit_nav": "1.234567", "amount": "0.152414703552",
            "execution_date": date(2026, 1, 1), "due_date": date(2026, 1, 2),
        }])
        self.assertEqual(first["account"]["cash"], "99.847585296448")
        self.assertEqual(first["account"]["pending_buys"][0]["value"], "0.152414703552")
        second = session.advance_day([], {})
        self.assertEqual(second["account"]["pending_buys"], [])
        self.assertEqual(second["account"]["holdings"][0], {
            "code": "000001", "shares": "0.123456", "unit_nav": "1.234567",
            "price_date": date(2026, 1, 1), "stale": True, "value": "0.152414703552",
        })
        third = session.advance_day([self.order("0.123456", "sell")], {"000001": "2"})
        self.assertEqual(third["account"]["holdings"], [])
        self.assertEqual(third["account"]["pending_cash"], [{
            "code": "000001", "amount": "0.246912000000",
            "execution_date": date(2026, 1, 3), "due_date": date(2026, 1, 4),
        }])
        terminal = session.advance_day([], {})
        self.assertTrue(terminal["finished"])
        self.assertEqual(terminal["account"]["pending_cash"], [])
        self.assertEqual(terminal["account"]["cash"], "100.094497296448")
        self.assertEqual(session.account()["account"], terminal["account"])
        before = session.account()
        self.assertEqual(before["status"], "finished")
        self.assertEqual(before["current_date"], date(2026, 1, 4))
        self.assertEqual(before["last_closed_date"], date(2026, 1, 4))
        with self.assertRaises(FinishedError):
            session.advance_day([], {})
        self.assertEqual(session.account(), before)

    def test_missing_nav_repair_and_effective_market_conflict(self):
        session = self.session()
        original = [self.order()]
        result = session.advance_day(original, {"000001": None})
        self.assertEqual(result, {
            "status": "rejected", "orders": [self.order("1.000000")],
            "nets": [self.order("1.000000")],
            "skips": [{**self.order("1.000000"), "reason": "missing_nav"}],
            "issues": [{"kind": "missing_closing_nav", "code": "000001"}],
        })
        self.assertEqual(set(result), set(fofoca_engine.Rejected.__annotations__))
        before = session.account()
        self.assertTrue(before["snapshot_pinned"])
        self.assertEqual(before["current_date"], date(2026, 1, 1))
        with self.assertRaises(MarketConflictError):
            session.advance_day(original, {"000001": "1"})
        self.assertEqual(session.account(), before)
        # Omitted keys and explicit None describe the same pinned market.
        repaired = session.advance_day([self.order(), self.order(side="sell")], {})
        self.assertEqual(repaired["executions"], [])
        self.assertFalse(session.account()["snapshot_pinned"])
        self.assertEqual(original, [self.order()])

    def test_business_issue_variants_and_normalized_retry(self):
        session = self.session(cash="1")
        market = MappingProxyType({"000001": "2", "000002": "1"})
        result = session.advance_day([self.order(), self.order(side="sell", code="000002")], market)
        self.assertEqual(result["issues"], [
            {"kind": "oversell", "code": "000002", "requested": "1.000000", "confirmed": "0.000000"},
            {"kind": "insufficient_cash", "required": "2.000000000000", "available": "1.000000000000"},
        ])
        retried = session.advance_day([MappingProxyType(self.order("0.5"))],
                                      {"000002": "1.000000", "000003": None, "000001": "2.0"})
        self.assertEqual(retried["status"], "advanced")
        overflow = self.session().advance_day([self.order("79228162514264337593543.950335")],
                                              {"000001": "1"})
        self.assertEqual(overflow["issues"], [{"kind": "arithmetic_overflow", "code": "000001"}])
        last = self.session(start=date.max, end=date.max)
        result = last.advance_day([self.order()], {"000001": "1"})
        self.assertEqual(result["issues"], [{"kind": "due_date_overflow"}])
        self.assertTrue(last.advance_day([], {"000001": "1"})["finished"])

    def test_daily_invalid_values_never_pin_or_change_state(self):
        malformed = [([], TypeError), ({}, TypeError), ({"code": "000001"}, TypeError)]
        for field in ("code", "side", "shares"):
            for value in (None, 1, 1.0, True, Decimal("1"), [], {}):
                malformed.append(({**self.order(), field: value}, TypeError))
        for value in ("", "12345", "1234567", "１２３４５６", "١٢٣٤٥٦", "12345a"):
            malformed.append(({**self.order(), "code": value}, ValueError))
        for value in ("BUY", "", "hold"):
            malformed.append(({**self.order(), "side": value}, ValueError))
        bad_decimals = ("0", "-1", "+1", "01", " 1", "1 ", "1e2", "NaN", "Infinity",
                        ".1", "1.", "１", "١", "1.0000000", "0.0000001")
        for value in bad_decimals:
            malformed.append(({**self.order(), "shares": value}, ValueError))
        malformed.append((self.order("9" * 100), OverflowError))
        bad_markets = [([], TypeError), (None, TypeError), ({1: "1"}, TypeError),
                       ({"12345": "1"}, ValueError)]
        for value in (1, 1.0, True, Decimal("1"), [], {}):
            bad_markets.append(({"000002": value}, TypeError))
        for value in bad_decimals:
            bad_markets.append(({"000002": value}, ValueError))
        bad_markets.append(({"000002": "9" * 100}, OverflowError))
        for native in (False, True):
            session = self.session()
            target = session._session if native else session
            before = session.account()
            for order, error in malformed:
                with self.subTest(native=native, order=order), self.assertRaises(error):
                    target.advance_day([self.order(), order], {"000001": "1"})
                self.assertEqual(session.account(), before)
            for market, error in bad_markets:
                with self.subTest(native=native, market=market), self.assertRaises(error):
                    target.advance_day([self.order()], market)
                self.assertEqual(session.account(), before)
            self.assertEqual(target.advance_day([], {})["status"], "advanced")

    def test_populated_results_are_recursively_detached(self):
        def corrupt(value):
            if isinstance(value, dict):
                for nested in list(value.values()):
                    corrupt(nested)
                value.clear()
            elif isinstance(value, list):
                for nested in value:
                    corrupt(nested)
                value.append({"invented": []})

        session = self.session()
        result = session.advance_day([self.order()], {"000001": "1"})
        before = session.account()
        corrupt(result)
        self.assertEqual(session.account(), before)
        for orders, market in (([], {}), ([self.order(side="sell")], {"000001": "1"})):
            result = session.advance_day(orders, market)
            before = session.account()
            corrupt(result)
            copy = session.account()
            corrupt(copy)
            self.assertEqual(session.account(), before)
        rejected = self.session()
        result = rejected.advance_day([self.order()], {})
        before = rejected.account()
        corrupt(result)
        self.assertEqual(rejected.account(), before)
        self.assertEqual(rejected.advance_day([], {})["status"], "advanced")

    def test_disposal_does_not_restore_account(self):
        session = BacktestSession()
        session.initialize(date.min, date.max, "1")
        del session
        with self.assertRaises(NotInitializedError):
            BacktestSession().account()


if __name__ == "__main__":
    unittest.main()
