"""Installed CPython 3.12 wheel only; also runs the unchanged engine contract."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.resources
import io
import json
import logging
from pathlib import Path
import runpy
import threading
import time
import unittest

# Reuse, never copy, all existing engine behavioral tests and their audit guard.
EngineContract = runpy.run_path(str(Path(__file__).resolve().parents[2] / "fofoca-engine/tests/wheel_contract.py"))["WheelContract"]

import fofoca_engine
import fofoca_engine._native as legacy_native
from fofoca_local import _native, data, engine


@contextmanager
def server(body, status=200, delay: float = 0, location=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            time.sleep(delay)
            encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", requests
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def config(url, timeout: float = 1):
    return data.Config(url, "user", "password", min(0.2, timeout), timeout)


def body(rows=()):
    return {"code": "000001", "start": "2026-01-01", "end": "2026-01-05", "rows": list(rows)}


def fetch(cfg):
    return data.fetch_nav_history("000001", date(2026, 1, 1), date(2026, 1, 5), config=cfg)


class LocalContract(unittest.TestCase):
    def test_single_native_compatibility_and_artifacts(self):
        self.assertIs(legacy_native, _native)
        self.assertIs(engine.BacktestSession, fofoca_engine.BacktestSession)
        self.assertIs(engine.NotInitializedError, fofoca_engine.NotInitializedError)
        self.assertIs(engine.Snapshot, fofoca_engine.Snapshot)
        self.assertIs(engine._native.Session, legacy_native.Session)
        self.assertIn("site-packages", _native.__file__)
        resources = importlib.resources.files("fofoca_local")
        for file in ("py.typed", "_native.pyi", "data.py", "engine/__init__.py"):
            self.assertTrue(resources.joinpath(file).is_file())
        native_files = [p for package in ("fofoca_local", "fofoca_engine")
                        for p in Path(str(importlib.resources.files(package))).rglob("*")
                        if p.suffix in {".so", ".pyd"}]
        self.assertEqual(len(native_files), 1)

    def test_typed_exact_records_and_explicit_composition(self):
        rows = [
            {"nav_date": "2026-01-01", "unit_nav": "1.230000", "accumulated_nav": "9.000001"},
            {"nav_date": "2026-01-03", "unit_nav": None, "accumulated_nav": "2.000000"},
            {"nav_date": "2026-01-04", "unit_nav": "0.100000", "accumulated_nav": None},
            {"nav_date": "2026-01-05", "unit_nav": None, "accumulated_nav": None},
        ]
        session = engine.BacktestSession()
        session.initialize(date(2026, 1, 1), date(2026, 1, 5), "100")
        before = session.account()
        with server(body(rows)) as (url, requests):
            history = fetch(config(url))
        self.assertEqual(requests, [("/funds/000001/nav?start=2026-01-01&end=2026-01-05", "Basic dXNlcjpwYXNzd29yZA==")])
        self.assertIsInstance(history, data.History)
        self.assertEqual(history.rows[0], data.NavRow(date(2026, 1, 1), "1.230000", "9.000001"))
        self.assertEqual([r.nav_date.day for r in history.rows], [1, 3, 4, 5])
        self.assertIsNone(history.rows[1].unit_nav)
        self.assertIsNone(history.rows[2].accumulated_nav)
        self.assertEqual(session.account(), before)
        result = session.advance_day([{"code": history.code, "side": "buy", "shares": "1"}],
                                     {history.code: history.rows[0].unit_nav})
        self.assertEqual(result["executions"][0]["unit_nav"], "1.230000")
        self.assertEqual(result["account"]["cash"], "98.770000000000")
        with server(body()) as (url, _):
            self.assertEqual(fetch(config(url)).rows, ())

    def test_typed_failures_and_diagnostic_redaction(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logging.getLogger().addHandler(handler)
        errors = []
        try:
            with redirect_stdout(output), redirect_stderr(output):
                for base in ("invalid-user-password", "http://user:password@localhost/", "https://example.test/?password"):
                    with self.assertRaises(data.ConfigurationError) as caught:
                        config(base)
                    errors.append(caught.exception)
                for timeout in (0, -1, float("nan"), float("inf"), 301):
                    with self.assertRaises(data.ConfigurationError) as caught:
                        config("http://127.0.0.1:1", timeout)
                    errors.append(caught.exception)
                cfg = config("http://127.0.0.1:1")
                self.assertEqual(repr(cfg), "Config([REDACTED])")
                with self.assertRaises(data.InputError):
                    data.fetch_nav_history("000001", datetime(2026, 1, 1), date.today(), config=cfg)
                with self.assertRaises(data.InputError):
                    data.fetch_nav_history("../abc", date(2026, 1, 1), date(2026, 1, 5), config=cfg)
                for response, status, error_type in ((b"user password Authorization", 401, data.HttpError),
                                                     (b"user password Authorization", 200, data.ResponseError),
                                                     ({**body(), "code": "000002"}, 200, data.ResponseError)):
                    with server(response, status) as (url, _):
                        with self.assertRaises(error_type) as caught:
                            fetch(config(url))
                        errors.append(caught.exception)
                with server(body(), delay=0.2) as (url, _):
                    with self.assertRaises(data.TransportError) as caught:
                        fetch(config(url, 0.05))
                    errors.append(caught.exception)
        finally:
            logging.getLogger().removeHandler(handler)
        text = output.getvalue() + "".join(str(e) + repr(e) for e in errors)
        for secret in ("user", "password", "Authorization", "dXNlcjpwYXNzd29yZA=="):
            self.assertNotIn(secret, text)

    def test_redirect_has_no_target_request(self):
        with server(body()) as (target, target_requests):
            with server(b"", 302, location=target + "/secret?password") as (origin, origin_requests):
                with self.assertRaises(data.HttpError):
                    fetch(config(origin))
            self.assertEqual(len(origin_requests), 1)
            self.assertEqual(target_requests, [])


if __name__ == "__main__":
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                               for cls in (EngineContract, LocalContract))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
