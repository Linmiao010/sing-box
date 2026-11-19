"""Serv00 recovery cases exercised through the shared installer/keeper implementation."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import serv00_runtime as runtime


class Serv00RecoveryTests(unittest.TestCase):
    def test_download_falls_back_once_then_cleans_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sb"
            dest.write_bytes(b"working")
            opener = Mock()
            opener.open.side_effect = OSError("offline")
            with patch.object(runtime.urllib.request, "build_opener", return_value=opener), patch.object(
                runtime, "run", side_effect=subprocess.CalledProcessError(1, ["curl"])
            ) as curl:
                with self.assertRaises(subprocess.CalledProcessError):
                    runtime.install_asset(dest, "sb", "0" * 64, runtime.fetch)
                self.assertEqual(curl.call_count, 1)
                self.assertIn("--max-time", curl.call_args.args[0])
            self.assertEqual(list(dest.parent.iterdir()), [dest])
            self.assertEqual(dest.read_bytes(), b"working")

    def test_primary_download_never_calls_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            lookup = Mock()
            lookup.open.return_value = io.BytesIO(b"complete")
            with patch.object(runtime.urllib.request, "build_opener", return_value=lookup), patch.object(runtime, "run") as curl:
                dest = Path(tmp) / "download"
                runtime.fetch("https://fixture.invalid/asset", dest)
            self.assertEqual(dest.read_bytes(), b"complete")
            curl.assert_not_called()

    def test_recovers_only_retryable_port_conflicts(self):
        for text, count in (("Port already in use", 5), ("Permission denied", 1),
                            ("port quota exceeded", 1), ("unknown error", 1)):
            with self.subTest(error=text), patch.object(runtime.subprocess, "run", return_value=Mock(
                returncode=1, stdout=text
            )) as allocate:
                with self.assertRaises(runtime.SetupError):
                    runtime.allocate("tcp")
                self.assertEqual(allocate.call_count, count)
        for kind in ("tcp", "udp"):
            with patch.object(runtime.subprocess, "run", side_effect=[Mock(returncode=1, stdout="already used"),
                                                                      Mock(returncode=0)]) as allocate:
                new = runtime.allocate(kind)
                self.assertTrue(10000 <= new <= 65535)
                self.assertEqual(allocate.call_count, 2)

    def test_existing_ports_preserved_and_failed_listing_stops_allocations(self):
        for items in ({"tcp": [10001,10002,12345], "udp": [20001,23456]}, None):
            with self.subTest(items=items), patch.object(runtime, "allocated_ports",
                side_effect=None if items else OSError, return_value=items), patch.object(runtime, "allocate") as allocate:
                config = {}
                if items:
                    runtime.choose_ports(config)
                    self.assertEqual(config, {"vless_port":10001, "vmess_port":10002, "hy2_port":20001})
                else:
                    with self.assertRaises(OSError):
                        runtime.choose_ports(config)
                allocate.assert_not_called()

    def test_failed_reset_restores_only_owned_allocations(self):
        state = dict(vless_port=10001, vmess_port=10002, hy2_port=20001, origin_https=True)
        with patch.object(runtime, "load", return_value=state), patch.object(
            runtime, "allocated_ports", return_value={"tcp":[10001,10002,12345], "udp":[20001]}
        ), patch("builtins.input", side_effect=["", "", ""]), patch.object(
            runtime, "allocate", side_effect=[11001, runtime.SetupError("quota")]
        ), patch.object(runtime, "run", return_value=Mock(returncode=0)) as devil, patch.object(runtime, "configure") as config:
            with self.assertRaises(runtime.SetupError):
                runtime.reset_ports()
        calls = [c.args[0][2:] for c in devil.call_args_list]
        self.assertEqual(calls, [["del","tcp","10001"], ["del","tcp","10002"], ["del","tcp","11001"],
                                 ["add","tcp","10001"], ["add","tcp","10002"]])
        config.assert_not_called()

    def test_ssh_keep_skips_fixed_tunnel_and_recovers_unhealthy_temporary_tunnel(self):
        for fixed, healthy in ((True, False), (False, True), (False, False)):
            with self.subTest(fixed=fixed, healthy=healthy), tempfile.TemporaryDirectory() as tmp:
                active = Path(tmp)
                (active/"argo-domain").write_text("active.trycloudflare.com")
                state = {"argo_token": "synthetic" if fixed else "", "argo_domain": "active.trycloudflare.com" if fixed else ""}
                with patch.object(runtime, "ACTIVE", active), patch.object(runtime, "load", return_value=state), patch.object(
                    runtime, "pid_of", return_value=123
                ), patch.object(runtime, "temp_argo", return_value="active.trycloudflare.com"), patch.object(
                    runtime, "temporary_tunnel_healthy", return_value=healthy
                ) as probe, patch.object(runtime, "ensure_running") as ensure, patch.object(runtime, "stop") as stop:
                    runtime.perform("keep")
                self.assertEqual(probe.call_count, 0 if fixed else 1)
                self.assertEqual(stop.call_count, int(not fixed and not healthy))
                ensure.assert_called_once()

    def test_http_error_404_is_a_healthy_tunnel_without_reading_content(self):
        for status, expected in ((404, True), (502, False)):
            lookup = Mock()
            lookup.open.side_effect = urllib.error.HTTPError("https://test.invalid", status, "", {}, io.BytesIO())
            with patch.object(runtime.urllib.request, "build_opener", return_value=lookup):
                self.assertEqual(runtime.temporary_tunnel_healthy("active.trycloudflare.com"), expected)
                self.assertFalse(runtime.temporary_tunnel_healthy("example.com"))


if __name__ == "__main__":
    unittest.main()
