"""Offline address discovery checks; never run the VPS installer or network."""
import shlex
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from shell_testlib import ShellTestCase, section


class IpDetectionTests(ShellTestCase):
    def probe(self, replies=None, addresses=""):
        replies = replies or {}
        script = section("v4v6", "warpcheck") + r'''
curl() {
    local family=4 url argument
    for argument in "$@"; do
        case "$argument" in
            -6|-s6m5) family=6 ;;
            -4|-s4m5) family=4 ;;
            *icanhazip.com*|*api64.ipify.org*) url=$argument ;;
        esac
    done
    printf '%s %s\n' "$family" "$url" >> requests
    case "$family:$url" in
'''
        for (family, host), (body, status) in replies.items():
            script += (f"{family}:*{host}*) printf '%s\\n' {shlex.quote(body)}; "
                       f"return {status} ;;\n")
        script += "*) return 22 ;;\nesac\n}\n"
        script += f"ip() {{ printf '%s\\n' {shlex.quote(addresses)}; }}\n"
        script += """
v4v6
printf '%s' "$v4" > v4
printf '%s' "$v6" > v6
printf '%s' "$v6_local" > v6_local
printf '%s' "$v6_display" > v6_display
"""
        self.run_shell(script)
        return {name: (self.root / name).read_text(encoding="utf-8")
                for name in ("v4", "v6", "v6_local", "v6_display")}

    def test_failed_primary_uses_ipv6_fallback(self):
        result = self.probe({
            (6, "icanhazip.com"): ("", 28),
            (6, "api64.ipify.org"): ("2607:f8b0::1234", 0),
        })
        self.assertEqual(result["v6"], "2607:f8b0::1234")
        self.assertEqual(result["v6_display"], "2607:f8b0::1234")
        self.assertIn("api64.ipify.org", (self.root / "requests").read_text())

    def test_successful_primary_keeps_both_address_families(self):
        result = self.probe({
            (4, "icanhazip.com"): ("203.0.113.7", 0),
            (6, "icanhazip.com"): ("2607:f8b0:0:131:216:3eff:fed4:720e", 0),
        })
        self.assertEqual(result["v4"], "203.0.113.7")
        self.assertEqual(result["v6"], "2607:f8b0:0:131:216:3eff:fed4:720e")
        self.assertNotIn("api64.ipify.org", (self.root / "requests").read_text())

    def test_interface_address_does_not_claim_public_connectivity(self):
        result = self.probe(addresses=
            "2: eth0 inet6 2607:f8b0::abcd/64 scope global dynamic valid_lft forever preferred_lft forever")
        self.assertEqual(result["v6"], "")
        self.assertEqual(result["v6_local"], "2607:f8b0::abcd")
        self.assertIn("本机已配置", result["v6_display"])
        self.assertIn("公网探测未成功", result["v6_display"])

    def test_error_pages_wrong_family_and_failed_partial_output_are_rejected(self):
        for body, status in (("<html>upstream error</html>", 0),
                             ("203.0.113.7", 0),
                             ("2607:f8b0::1234", 28),
                             ("2607:invalid::xyz", 0),
                             ("2607:::1", 0),
                             ("2607::1::2", 0),
                             (":1::2", 0),
                             ("1::2:", 0),
                             ("::1:", 0),
                             ("2607:1:2:3:4:5:6:7:8", 0)):
            with self.subTest(body=body, status=status):
                result = self.probe({(6, host): (body, status)
                                     for host in ("icanhazip.com", "api64.ipify.org")})
                self.assertEqual(result["v6"], "")
        result = self.probe({(4, host): ("2607:f8b0::1234", 0)
                             for host in ("icanhazip.com", "api64.ipify.org")})
        self.assertEqual(result["v4"], "")

    def test_failed_install_probe_preserves_dns_and_restores_warp(self):
        resolver = self.root / "resolv.conf"
        original = "nameserver 192.0.2.53\n"
        resolver.write_text(original, encoding="utf-8")
        script = (section("ip_literal_valid", "warpcheck") + "\n" + section("v6", "close"))
        script = script.replace("/etc/resolv.conf", resolver.as_posix())
        script = script.replace("/etc/s-box", (self.root / "s-box").as_posix())
        script += r'''
public_ip() { return 1; }
warpcheck() { wgcfv4=on; wgcfv6=on; }
red() { :; }
yellow() { :; }
systemctl() { printf '%s\n' "$*" >> lifecycle; }
pgrep() { echo 123; }
kill() { :; }
sleep() { :; }
v6
'''
        result = self.run_shell(script, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(resolver.read_text(), original)
        lifecycle = (self.root / "lifecycle").read_text()
        self.assertIn("start wg-quick@wgcf", lifecycle)
        self.assertIn("restart warp-go", lifecycle)

    def test_unusable_interface_addresses_are_skipped(self):
        rows = [f"2: eth0 inet6 2607:f8b0::{index}/64 scope global {state} valid_lft forever preferred_lft forever"
                for index, state in enumerate(("tentative", "dadfailed", "deprecated"), 1)]
        rows.append("2: eth0 inet6 2607:f8b0::4/64 scope global dynamic valid_lft forever preferred_lft forever")
        result = self.probe(addresses="\n".join(rows))
        self.assertEqual(result["v6_local"], "2607:f8b0::4")
        self.assertEqual(result["v6"], "")

    def test_both_families_and_interface_have_no_address(self):
        result = self.probe()
        self.assertEqual(result["v4"], "")
        self.assertEqual(result["v6"], "")
        self.assertEqual(result["v6_local"], "")
        self.assertTrue(result["v6_display"])

    def refresh(self, ipv4="", ipv6="", selection=None, warp=""):
        config = self.root / "config"
        config.mkdir(exist_ok=True)
        (config / "sb.json").write_text("{}", encoding="utf-8")
        for name in ("server_ip.log", "server_ipcl.log"):
            (config / name).write_text("previous-address\n", encoding="utf-8")
        script = section("ipuuid", "wgcfgo").replace("/etc/s-box", config.as_posix())
        script += f"\nv4={shlex.quote(ipv4)}; v6={shlex.quote(ipv6)}\n"
        if selection is None:
            script += f"v4v6() {{ v4={shlex.quote(ipv4)}; v6={shlex.quote(ipv6)}; }}\n"
        else:
            script += 'v4v6() { echo unexpected > unexpected-probe; }\n'
        script += f"readp() {{ menu={shlex.quote(selection or '')}; }}\n"
        script += ('ip() { printf "%s\\n" "3: wgcf: <POINTOPOINT,UP,LOWER_UP>"; }\n'
                   if warp == "wgcf" else 'ip() { return 1; }\n')
        script += ('pgrep() { echo 123; }\n' if warp == "warp-go" else 'pgrep() { return 1; }\n')
        script += r'''
systemctl() { echo active; }
rc-service() { echo started; }
red() { :; }
yellow() { :; }
green() { :; }
curl() { echo unexpected >> unexpected-curl; return 22; }
'''
        script += "ipuuid" + (" select" if selection is not None else "") + "\n"
        return self.run_shell(script, check=False), config

    def test_output_selection_uses_current_public_results_without_probe(self):
        for choice, expected, plain in (("1", "203.0.113.7", "203.0.113.7"),
                                       ("2", "[2607:f8b0::7]", "2607:f8b0::7")):
            with self.subTest(choice=choice):
                result, config = self.refresh("203.0.113.7", "2607:f8b0::7", choice)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((config / "server_ip.log").read_text().strip(), expected)
                self.assertEqual((config / "server_ipcl.log").read_text().strip(), plain)
                self.assertFalse((self.root / "unexpected-probe").exists())

    def test_unavailable_selected_family_preserves_addresses(self):
        for ipv4, ipv6, choice in (("203.0.113.7", "", "2"),
                                  ("", "2607:f8b0::7", "1")):
            with self.subTest(choice=choice):
                result, config = self.refresh(ipv4, ipv6, choice)
                self.assertNotEqual(result.returncode, 0)
                for name in ("server_ip.log", "server_ipcl.log"):
                    self.assertEqual((config / name).read_text(), "previous-address\n")

    def test_invalid_output_selection_preserves_addresses(self):
        result, config = self.refresh("203.0.113.7", "2607:f8b0::7", "invalid")
        self.assertNotEqual(result.returncode, 0)
        for name in ("server_ip.log", "server_ipcl.log"):
            self.assertEqual((config / name).read_text(), "previous-address\n")

    def test_global_warp_output_selection_preserves_inbound_addresses(self):
        for warp in ("wgcf", "warp-go"):
            with self.subTest(warp=warp):
                result, config = self.refresh("203.0.113.7", "2607:f8b0::7", "1", warp)
                self.assertNotEqual(result.returncode, 0)
                for name in ("server_ip.log", "server_ipcl.log"):
                    self.assertEqual((config / name).read_text(), "previous-address\n")

    def test_failed_refresh_preserves_existing_node_addresses(self):
        result, config = self.refresh()
        self.assertNotEqual(result.returncode, 0)
        for name in ("server_ip.log", "server_ipcl.log"):
            self.assertEqual((config / name).read_text(), "previous-address\n")

    def test_single_stack_refresh_reuses_detected_address(self):
        for ipv4, ipv6, expected in (("203.0.113.7", "", "203.0.113.7"),
                                     ("", "2607:f8b0::7", "[2607:f8b0::7]")):
            with self.subTest(ipv4=ipv4, ipv6=ipv6):
                result, config = self.refresh(ipv4, ipv6)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((config / "server_ip.log").read_text().strip(), expected)
                self.assertEqual((config / "server_ipcl.log").read_text().strip(), ipv4 or ipv6)
                self.assertFalse((self.root / "unexpected-curl").exists())

    def test_failed_refresh_restores_warp_and_reports_failure(self):
        script = section("wgcfgo", "result_vl_vm_hy_tu") + r'''
warpcheck() { wgcfv4=on; wgcfv6=off; }
ipuuid() { echo refresh-failed >> lifecycle; return 7; }
systemctl() { printf '%s\n' "$*" >> lifecycle; }
pgrep() { echo 123; }
kill() { :; }
sleep() { :; }
wgcfgo
'''
        result = self.run_shell(script, check=False)
        self.assertNotEqual(result.returncode, 0)
        lifecycle = (self.root / "lifecycle").read_text().splitlines()
        self.assertIn("stop wg-quick@wgcf", lifecycle)
        failure = lifecycle.index("refresh-failed")
        self.assertGreater(lifecycle.index("start wg-quick@wgcf"), failure)
        self.assertGreater(lifecycle.index("restart warp-go"), failure)


class MenuIpStatusTests(ShellTestCase):
    """Use real curl against loopback HTTP; do not contact public probe sites."""

    def status(self, mode, samples=1):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                family = self.headers["X-Probe-Family"]
                requests.append((family, self.path))
                if mode == "timeout" or (mode == "fallback" and self.path == "/primary"):
                    time.sleep(4)
                elif mode == "delayed":
                    time.sleep(1.2)
                body = ("203.0.113.25" if family == "4" else "2001:db8::25").encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # A timed-out curl deliberately closes the connection.

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        script = section("v4v6", "warpcheck") + r'''
curl() {
    local argument family=4 endpoint
    local -a options=()
    for argument in "$@"; do
        case "$argument" in
        -4) family=4 ;;
        -6) family=6 ;;
        https://icanhazip.com) endpoint=primary ;;
        https://api64.ipify.org) endpoint=fallback ;;
        *) options+=("$argument") ;;
        esac
    done
    command curl "${options[@]}" -4 --header "X-Probe-Family: $family" "$PROBE_BASE/$endpoint"
}
ip() { echo unexpected > interface-read; }
for ((sample=0; sample < SAMPLES; sample++)); do
    v4=198.51.100.99 v6=2001:db8::99
    started=$EPOCHREALTIME
    menu_ip_status
    printf '%s %s\n' "$started" "$EPOCHREALTIME" >> timings
    printf '%s\n' "$vps_ipv4" "$vps_ipv6"
done
'''
        try:
            result = self.run_shell(script, env={
                "PROBE_BASE": f"http://127.0.0.1:{server.server_port}", "SAMPLES": str(samples),
            })
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        durations = [float(end) - float(start) for start, end in
                     (line.split() for line in (self.root / "timings").read_text().splitlines())]
        self.assertFalse((self.root / "interface-read").exists())
        return result.stdout, requests, durations

    def test_menu_status_displays_only_external_probe_addresses(self):
        output, requests, durations = self.status("success")
        self.assertEqual(output.splitlines(), ["203.0.113.25", "2001:db8::25"])
        self.assertCountEqual(requests, [(family, path) for family in ("4", "6")
                                        for path in ("/primary", "/fallback")])
        self.assertLess(max(durations), 3.0, durations)

    def test_menu_status_accepts_slow_response_within_three_seconds(self):
        output, requests, durations = self.status("delayed")
        self.assertEqual(output.splitlines(), ["203.0.113.25", "2001:db8::25"])
        self.assertEqual(len(requests), 4)
        self.assertLess(max(durations), 3.0, durations)

    def test_menu_status_falls_back_within_three_seconds(self):
        output, requests, durations = self.status("fallback")
        self.assertEqual(output.splitlines(), ["203.0.113.25", "2001:db8::25"])
        self.assertCountEqual(requests, [(family, path) for family in ("4", "6")
                                        for path in ("/primary", "/fallback")])
        self.assertLess(max(durations), 3.0, durations)

    def test_dual_stack_timeout_finishes_within_three_seconds_without_stale_results(self):
        output, requests, durations = self.status("timeout", samples=3)
        self.assertEqual(output.splitlines(), ["公网IPv4探测未成功", "公网IPv6探测未成功"] * 3)
        self.assertEqual(len(requests), 12)
        self.assertLess(max(durations), 3.0, durations)
