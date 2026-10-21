"""Run using an isolated interpreter with the real wheel installed, not PYTHONPATH."""
import importlib.resources
import os
from pathlib import Path
import sys
import unittest
from datetime import date


def no_child_process(event, args):
    if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.fork", "os.exec"}:
        raise AssertionError(f"Runtime must not start a child process: {event}")


# Prohibit real child-process operations before importing the installed package.
sys.addaudithook(no_child_process)
os.environ["PATH"] = ""

import fofoca_engine
from fofoca_engine import BacktestSession, NotInitializedError
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
        self.assertIs(NotInitializedError, _native.NotInitializedError)
        self.assertEqual(NotInitializedError.__module__, "fofoca_engine._native")
        self.assertTrue(issubclass(NotInitializedError, RuntimeError))

    def test_initialize_never_changes_state(self):
        session = BacktestSession()
        for _ in range(2):
            with self.assertRaises(NotImplementedError):
                session.initialize(date(2026, 1, 1), date(2026, 1, 2), "100")
            with self.assertRaises(NotInitializedError):
                session.account()
            with self.assertRaises(NotInitializedError):
                session.report()

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
                     (today, today, 100.0), (today, today, 100)):
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
        with self.assertRaises(NotImplementedError):
            session.initialize(date(2026, 1, 1), date(2026, 1, 2), "100")
        with self.assertRaises(_native.NotInitializedError):
            session.report()


if __name__ == "__main__":
    unittest.main()
