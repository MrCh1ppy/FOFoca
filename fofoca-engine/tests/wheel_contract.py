"""Run using an isolated interpreter with the real wheel installed, not PYTHONPATH."""
import importlib.resources
import os
from pathlib import Path
import sys
import unittest
from datetime import date, datetime
from decimal import Decimal


def no_child_process(event, args):
    if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.fork", "os.exec"}:
        raise AssertionError(f"Runtime must not start a child process: {event}")


# Prohibit real child-process operations before importing the installed package.
sys.addaudithook(no_child_process)
os.environ["PATH"] = ""

import fofoca_engine
from fofoca_engine import AlreadyInitializedError, BacktestSession, NotInitializedError
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
        for error in (NotInitializedError, AlreadyInitializedError):
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
            (session.set_orders, ([],)),
            (session.advance_day, ({"date": date(2026, 1, 1), "unit_nav": {}},)),
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
        with self.assertRaises(TypeError):
            session.set_orders({})
        with self.assertRaises(TypeError):
            session.advance_day([])

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

    def test_initialized_trading_and_report_still_unimplemented(self):
        session = BacktestSession()
        today = date(2026, 1, 1)
        session.initialize(today, today, "100")
        before = session.account()
        for method, args in ((session.set_orders, ([],)),
                             (session.advance_day, ({"date": today, "unit_nav": {}},)),
                             (session.report, ())):
            with self.subTest(method=method.__name__), self.assertRaises(NotImplementedError):
                method(*args)
            self.assertEqual(session.account(), before)

    def test_disposal_does_not_restore_account(self):
        session = BacktestSession()
        session.initialize(date.min, date.max, "1")
        del session
        with self.assertRaises(NotInitializedError):
            BacktestSession().account()


if __name__ == "__main__":
    unittest.main()
