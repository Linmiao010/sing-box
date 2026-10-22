"""Offline address discovery checks; never run the VPS installer or network."""
import shlex

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
        script = section("v6", "close").replace("/etc/resolv.conf", resolver.as_posix())
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

    def refresh(self, ipv4="", ipv6=""):
        config = self.root / "config"
        config.mkdir(exist_ok=True)
        (config / "sb.json").write_text("{}", encoding="utf-8")
        for name in ("server_ip.log", "server_ipcl.log"):
            (config / name).write_text("previous-address\n", encoding="utf-8")
        script = section("ipuuid", "wgcfgo").replace("/etc/s-box", config.as_posix())
        script += f"\nv4v6() {{ v4={shlex.quote(ipv4)}; v6={shlex.quote(ipv6)}; }}\n"
        script += r'''
systemctl() { echo active; }
rc-service() { echo started; }
red() { :; }
yellow() { :; }
green() { :; }
curl() { echo unexpected >> unexpected-curl; return 22; }
ipuuid
'''
        return self.run_shell(script, check=False), config

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
