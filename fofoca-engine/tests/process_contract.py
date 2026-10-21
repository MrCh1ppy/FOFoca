"""Offline process contracts; every subprocess wait/read has a deadline."""
import json
import os
import select
import subprocess
import sys
import time
import unittest

BINARY = sys.argv.pop(1)


def request(command="quit", params=None, request_id: object = "r"):
    return json.dumps({"request_id": request_id, "command": command,
                       "params": {} if params is None else params})


class ProcessContract(unittest.TestCase):
    def exchange(self, lines, final_newline=True):
        payload = "\n".join(lines) + ("\n" if lines and final_newline else "")
        result = subprocess.run([BINARY], input=payload.encode(), capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        if result.stdout:
            self.assertTrue(result.stdout.endswith(b"\n"))
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        for response in responses:
            self.assertEqual(set(response), {"request_id", "ok", "data", "error", "state"})
            self.assertIsNone(response["state"])
            if not response["ok"]:
                self.assertIsNone(response["data"])
                self.assertEqual(set(response["error"]), {"code", "message", "raw_request", "raw_line",
                                 "submitted_orders", "net_operations", "skips", "issues"})
                for issue in response["error"]["issues"]:
                    self.assertEqual(set(issue), {"code", "path", "message", "code_context", "details"})
        return responses

    def test_quit(self):
        self.assertEqual(self.exchange([request()]), [dict(request_id="r", ok=True, data={}, error=None, state=None)])

    def test_empty_eof(self):
        self.assertEqual(self.exchange([]), [])

    def test_unterminated_final_line(self):
        self.assertTrue(self.exchange([request()], False)[0]["ok"])

    def test_blank_malformed_continue(self):
        responses = self.exchange(["", "  ", "{", request()])
        self.assertEqual([r["error"]["code"] for r in responses[:-1]], ["invalid_json"] * 3)
        self.assertTrue(responses[-1]["ok"])
        self.assertEqual(responses[1]["error"]["raw_line"], "  ")

    def test_unknown_command(self):
        response, = self.exchange([request("unknown")])
        self.assertEqual(response["error"]["code"], "unknown_command")
        self.assertEqual(response["request_id"], "r")

    def test_invalid_envelopes(self):
        lines = ['[]', '{}', request(request_id=""), request(request_id=4),
                 '{"request_id":"x","command":"quit"}',
                 '{"request_id":"x","command":"quit","params":{},"extra":0}',
                 request(params=[]), request(params={"extra": 0})]
        for response in self.exchange(lines):
            self.assertEqual(response["error"]["code"], "invalid_request")

    def test_command_shapes(self):
        lines = [request("init"), request("init", {"start_date":"x", "end_date":"y", "initial_cash":1}),
                 request("set_orders", {"orders":[{"code":"000001","side":"hold","shares":"1"}]}),
                 request("set_orders", {"orders":[{"code":"000001","side":"buy","shares":"1","extra":0}]}),
                 request("set_orders", {"orders":[{"code":"000001","side":"buy","shares":1.2}]}),
                 request("advance_day", {"market":{"date":"x","unit_nav":{"000001":1}}}),
                 request("advance_day", {"market":{"date":"x","unit_nav":{},"extra":0}})]
        for response in self.exchange(lines):
            self.assertEqual(response["error"]["code"], "invalid_request")

    def test_recursive_duplicates(self):
        lines = ['{"request_id":"x","request_id":"y"}',
                 '{"request_id":"x","params":{"orders":[{"a":1,"a":2}]}}',
                 '{"request_id":"x","params":{"market":{"unit_nav":{"000001":"1","000001":null}}}}',
                 '{"request_id":"x","params":{"a":1,"\\u0061":2}}']
        for line, response in zip(lines, self.exchange(lines)):
            self.assertEqual(response["error"]["code"], "duplicate_key")
            self.assertIsNone(response["request_id"])
            self.assertIsNone(response["error"]["raw_request"])
            self.assertEqual(response["error"]["raw_line"], line)

    def test_id_recovery(self):
        lines = ['{"request_id":"id","command":0}', '{"request_id":"id"} trailing',
                 '{"request_id":null}', '{"request_id":"中文","command":"unknown","params":{}}']
        self.assertEqual([r["request_id"] for r in self.exchange(lines)], ["id", None, None, "中文"])

    def test_business_never_creates_state(self):
        lines = [request("init", {"start_date":"2026-01-01", "end_date":"2026-01-02", "initial_cash":"100"}),
                 request("account"), request("set_orders", {"orders":[]}),
                 request("advance_day", {"market":{"date":"2026-01-01", "unit_nav":{"000001":None}}}),
                 request("report")]
        responses = self.exchange(lines + lines)
        self.assertEqual([r["error"]["code"] for r in responses],
                         ["not_implemented"] + ["not_initialized"] * 4 + ["not_implemented"] + ["not_initialized"] * 4)
        for response in responses:
            self.assertIsNone(response["error"]["net_operations"])
            self.assertIsNone(response["error"]["skips"])

    def test_quit_stops_processing(self):
        self.assertEqual(len(self.exchange([request(), "invalid", request("report")])), 1)

    def test_crlf_and_raw_line(self):
        response, = self.exchange(["bad\r"])
        self.assertEqual(response["error"]["raw_line"], "bad")

    def test_flush_with_stdin_open(self):
        process = subprocess.Popen([BINARY], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write((request("account") + "\n").encode())
            process.stdin.flush()
            output = b""
            deadline = time.monotonic() + 5
            while b"\n" not in output:
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0, "response was not flushed while stdin remained open")
                ready, _, _ = select.select([process.stdout], [], [], remaining)
                self.assertTrue(ready, "response timeout")
                chunk = os.read(process.stdout.fileno(), 4096)
                self.assertTrue(chunk, "unexpected EOF")
                output += chunk
            self.assertEqual(json.loads(output)["error"]["code"], "not_initialized")
            self.assertIsNone(process.poll())
            stdout, stderr = process.communicate((request() + "\n").encode(), timeout=5)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(stderr, b"")
            self.assertTrue(json.loads(stdout)["ok"])
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def test_invalid_utf8_fails_safely(self):
        result = subprocess.run([BINARY], input=b"\xff\n", capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"transport I/O failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
