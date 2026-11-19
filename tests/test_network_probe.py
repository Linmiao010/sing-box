"""Probe real curl against a small loopback fixture; no external traffic."""
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "network_probe.py"
BODY = b"probe-fixture-" * 512


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        with self.server.guard:
            self.server.active += 1
            self.server.maximum = max(self.server.maximum, self.server.active)
            self.server.targets.append(self.path)
        try:
            path = urlsplit(self.path).path
            if path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/ok?private_redirect=secret")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path == "/slow":
                time.sleep(0.1)
            if path == "/wait":
                time.sleep(0.2)
                return
            body = b"error" if path == "/error" else BODY
            self.send_response(503 if path == "/error" else 200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            with self.server.guard:
                self.server.active -= 1

    def log_message(self, *args):
        pass


@unittest.skipUnless(shutil.which("curl"), "curl is required for loopback probe checks")
class NetworkProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="network-probe-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "results.csv"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.guard = threading.Lock()
        self.server.active = self.server.maximum = 0
        self.server.targets = []
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def probe(self, path="/ok", count=1, extra=(), label="A1", env=None):
        variables = os.environ.copy()
        variables.update(env or {})
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--label", label,
             "--url", self.base + path, "--output", str(self.output),
             "--count", str(count), *extra],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, env=variables,
        )
        return result

    def rows(self):
        with self.output.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_redirect_stops_at_first_response_and_keeps_secrets_out_of_metrics(self):
        result = self.probe("/redirect?token=PRIVATE_URL_TOKEN")
        self.assertEqual(result.returncode, 1, result.stderr)
        row = self.rows()[0]
        self.assertEqual(int(row["http_code"]), 302)
        self.assertEqual(int(row["curl_exit_code"]), 0)
        self.assertEqual(int(row["num_redirects"]), 0)
        self.assertEqual(float(row["size_download"]), 0)
        self.assertEqual(row["success"], "0")
        self.assertEqual(self.server.targets, ["/redirect?token=PRIVATE_URL_TOKEN"])
        for field in ("time_namelookup", "time_connect", "time_appconnect",
                      "time_starttransfer", "time_total", "time_redirect",
                      "speed_download", "request_wall_seconds"):
            self.assertGreaterEqual(float(row[field]), 0, field)
        output = result.stdout + result.stderr + self.output.read_text(encoding="utf-8")
        for private in ("PRIVATE_URL_TOKEN", "private_redirect", self.base):
            self.assertNotIn(private, output)

    def test_system_path_ignores_proxy_environment(self):
        result = self.probe(extra=("--system-path",), env={
            "http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1",
            "ALL_PROXY": "http://127.0.0.1:1", "all_proxy": "http://127.0.0.1:1",
            "NO_PROXY": "", "no_proxy": "",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rows()[0]["path"], "system")

    def test_explicit_proxy_overrides_no_proxy_without_logging_credentials(self):
        proxy = self.base.replace("http://", "http://PRIVATE_USER:PRIVATE_PASSWORD@")
        result = self.probe("/ok?token=PRIVATE_QUERY", extra=("--proxy", proxy),
                            env={"NO_PROXY": "*", "no_proxy": "*"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.server.targets[0].startswith("http://"))
        self.assertEqual(self.rows()[0]["path"], "proxy")
        output = result.stdout + result.stderr + self.output.read_text(encoding="utf-8")
        for private in ("PRIVATE_USER", "PRIVATE_PASSWORD", "PRIVATE_QUERY", self.base):
            self.assertNotIn(private, output)

    def test_http_failure_is_recorded_but_excluded_from_success_statistics(self):
        result = self.probe("/error?token=PRIVATE_ERROR")
        self.assertEqual(result.returncode, 1, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["requests"], 1)
        self.assertEqual(summary["failed_requests"], 1)
        self.assertEqual(summary["time_total_seconds"]["n"], 0)
        self.assertEqual(int(self.rows()[0]["http_code"]), 503)
        self.assertNotIn("PRIVATE_ERROR", result.stdout + result.stderr)

    def test_fixed_concurrency_uses_batch_bytes_divided_by_walltime(self):
        result = self.probe("/slow", count=2, extra=("--concurrency", "3"))
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = self.rows()
        self.assertEqual(len(rows), 6)
        self.assertEqual(self.server.maximum, 3)
        for batch in (1, 2):
            batch_rows = [row for row in rows if int(row["batch"]) == batch]
            self.assertEqual(len(batch_rows), 3)
            row = batch_rows[0]
            self.assertEqual(float(row["batch_size_download"]), 3 * len(BODY))
            self.assertAlmostEqual(
                float(row["batch_speed_download"]),
                3 * len(BODY) / float(row["batch_wall_seconds"]), places=4,
            )
        summary = json.loads(result.stdout)
        self.assertEqual(summary["total_size_download"], 6 * len(BODY))
        self.assertEqual(summary["batch_speed_download_Bps"]["n"], 2)

    def test_repeated_labels_append_distinct_runs_for_abab(self):
        first = self.probe(label="A")
        second = self.probe(label="A")
        self.assertEqual((first.returncode, second.returncode), (0, 0))
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["run_id"], rows[1]["run_id"])

    def test_p95_is_only_reported_with_thirty_successful_samples(self):
        for count in (2, 30):
            with self.subTest(count=count):
                result = self.probe(count=count)
                self.assertEqual(result.returncode, 0, result.stderr)
                stats = json.loads(result.stdout)["time_total_seconds"]
                self.assertEqual(stats["n"], count)
                self.assertEqual("p95" in stats, count >= 30)
                self.assertLessEqual(stats["min"], stats["p50"])
                self.assertLessEqual(stats["p50"], stats["max"])

    def test_expected_bytes_rejects_wrong_sized_response(self):
        result = self.probe(extra=("--expected-bytes", str(len(BODY) + 1)))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["failed_requests"], 1)
        self.assertEqual(self.rows()[0]["success"], "0")

    def test_curl_timeout_preserves_exit_code_and_failure_count(self):
        result = self.probe("/wait?token=PRIVATE_TIMEOUT", extra=("--timeout", "0.02"))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(int(self.rows()[0]["curl_exit_code"]), 28)
        self.assertEqual(json.loads(result.stdout)["failed_requests"], 1)
        self.assertNotIn("PRIVATE_TIMEOUT", result.stdout + result.stderr)

    def test_incompatible_csv_is_not_overwritten_or_appended(self):
        original = "old,unrelated,header\n"
        self.output.write_text(original, encoding="utf-8")
        result = self.probe()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.output.read_text(encoding="utf-8"), original)
        self.assertEqual(self.server.targets, [])

    def test_invalid_arguments_do_not_echo_url_secrets(self):
        result = self.probe(extra=("--concurrency", "PRIVATE_INVALID_VALUE"))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("PRIVATE_INVALID_VALUE", result.stdout + result.stderr)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
