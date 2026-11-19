"""Exercise the release gate and the firewall policy without touching the host firewall."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

from shell_testlib import REPO, ShellTestCase, section


def module():
    spec = importlib.util.spec_from_file_location("sbox_firewall", REPO / "tools/firewall.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def nodes(path, udp=3001):
    data = [{"tag": "vless-sb", "listen": "192.0.2.8", "listen_port": 2443},
            {"tag": "vmess-sb", "listen": "127.0.0.1", "listen_port": 8899},
            {"tag": "hy2-sb", "listen": "::", "listen_port": udp},
            {"tag": "tuic5-sb", "listen": "::1", "listen_port": 4444}]
    (path / "sb.json").write_text(json.dumps({"inbounds": data}))
    (path / "sb_extra.json").write_text(json.dumps({"inbounds": [
        {"tag": "trojan-sb", "listen": "2001:db8::8", "listen_port": 2443}]}))


class FakeIptables:
    """Implements exact-spec iptables operations; rejects global mutations."""
    def __init__(self):
        self.tables = {}
        self.policy = {}
        self.sockets = {4: "", 6: ""}
        for binary in ("iptables", "ip6tables"):
            self.policy[binary] = "DROP"
            self.tables[binary, "filter", "INPUT"] = [("-p", "tcp", "--dport", "2222", "-j", "ACCEPT")]
            self.tables[binary, "nat", "PREROUTING"] = [
                ("-p", "udp", "--dport", "1235", "-j", "DNAT", "--to-destination", ":1234")]

    def __call__(self, binary, *params, check=True):
        if binary == "ss":
            return subprocess.CompletedProcess([], 0, self.sockets[int(params[0][1:])], "")
        if binary == "nft":
            return subprocess.CompletedProcess([], 0, '{"nftables":[]}', "")
        wait, seconds, opt, table, action, *tail = params
        assert (wait, opt) == ("-w", "-t")
        if not tail and action == "-S":
            listing = [shlex.join(["-A", chain, *entry]) for (tool, tbl, chain), entries in self.tables.items()
                       if tool == binary and tbl == table for entry in entries]
            return subprocess.CompletedProcess([], 0, "\n".join(listing), "")
        chain, *args = tail
        args = tuple(args)
        rules = self.tables[binary, table, chain]
        result = 0
        if action == "-C":
            result = 0 if args in rules else 1
        elif action == "-A":
            rules.append(args)
        elif action == "-D":
            rules.remove(args)
        elif action == "-S":
            text = "\n".join(shlex.join(["-A", chain, *entry]) for entry in rules)
            return subprocess.CompletedProcess([], 0, text, "")
        else:
            raise AssertionError(action)
        return subprocess.CompletedProcess([], result, "", "")


class FirewallTests(unittest.TestCase):
    def test_cli_upgrades_legacy_state_and_clears_only_selected_hops(self):
        fw, fake = module(), FakeIptables()
        with tempfile.TemporaryDirectory() as tmp, patch.object(fw, "ROOT", Path(tmp)), \
             patch.object(fw, "cmd", fake), patch.object(fw, "backend", return_value="iptables"):
            nodes(fw.ROOT)
            def cli(*args):
                with patch.object(sys, "argv", ["firewall.py", *args]), patch.dict(sys.modules, {
                        "fcntl": types.SimpleNamespace(LOCK_EX=1, flock=lambda *_: None)}):
                    return fw.main()
            cli("enable", "--input-position", "append")
            cli("add", "hy2-sb", "4000:4005")
            path = fw.ROOT / "firewall.json"
            state = json.loads(path.read_text())
            state.pop("append_backend")
            path.write_text(json.dumps(state))
            # Simulate a deployed old head rule. A legacy sync withdraws its allow,
            # retains administrator rules & valid hops, and never inserts at the head.
            chain = fake.tables["iptables", "filter", "INPUT"]
            chain.reverse()
            with patch("sys.stderr", new_callable=io.StringIO) as warning:
                cli("sync")
                self.assertIn("手动放行", warning.getvalue())
            self.assertFalse(any(fw.MARK in r for r in chain))
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                cli("list", "hy2-sb")
                self.assertEqual(output.getvalue().strip(), "4000:4005")
            cli("enable", "--input-position", "append")
            self.assertTrue(all(fw.MARK in r for r in chain[1:]))
            cli("clear", "hy2-sb")
            self.assertNotIn("4000:4005", path.read_text())
            cli("remove")
            self.assertFalse(path.exists())
            self.assertIn("2222", str(chain))

    def test_ipv6_forwards_and_socket_inventory_errors_abort_before_writes(self):
        fw, fake = module(), FakeIptables()
        with tempfile.TemporaryDirectory() as tmp, patch.object(fw, "ROOT", Path(tmp)), \
             patch.object(fw, "backend", return_value="iptables"):
            (fw.ROOT / "sb.json").write_text(json.dumps({"inbounds": [
                {"tag": "hy2-sb", "listen": "2001:db8::8", "listen_port": 3000}]}))
            table = fake.tables["ip6tables", "nat", "PREROUTING"]
            table.append(("-p", "udp", "-d", "2001:db8::/64", "-m", "multiport", "--dports", "4000:4020,4050",
                          "-j", "DNAT", "--to", "[2001:db8:1::2]:10000"))
            with patch.object(fw, "cmd", fake):
                fw.manage("add", "hy2-sb", "4021:4049")
            path = fw.ROOT / "firewall.json"
            old = path.read_bytes()
            rules = copy.deepcopy(fake.tables)
            with patch.object(fw, "cmd", fake), self.assertRaisesRegex(ValueError, "端口 4019:4020"):
                fw.manage("add", "hy2-sb", "4019:4021")
            for listing in (FileNotFoundError("ss"), subprocess.CompletedProcess([], 0, "", "incomplete")):
                def unreadable(*args, **kwargs):
                    if args[0] == "ss":
                        if isinstance(listing, Exception):
                            raise listing
                        return listing
                    return fake(*args, **kwargs)
                with self.subTest(listing=listing), patch.object(fw, "cmd", unreadable), \
                     self.assertRaises((OSError, ValueError)):
                    fw.manage("sync")
            self.assertEqual(old, path.read_bytes())
            self.assertEqual(rules, fake.tables)

    def test_nft_dnat_interval_map_keeps_distinct_destinations_and_ports(self):
        fw = module()
        fields = {"concat": [{"payload": {"protocol": "ip", "field": "daddr"}},
                             {"payload": {"protocol": "udp", "field": "dport"}}]}
        objects = [{"map": {"family": "inet", "table": "policy", "name": "services", "flags": ["interval"],
                            "elem": [[{"concat": ["192.0.2.8", {"range": [4000, 4050]}]}, "192.0.2.20"],
                                     [{"concat": ["192.0.2.9", 4100]}, "192.0.2.21"]]}},
                   {"rule": {"family": "inet", "table": "policy", "chain": "prerouting", "expr": [
                       {"dnat": {"family": "ip", "addr": {"map": {"key": fields, "data": "@services"}}}}]}}]
        with patch.object(fw, "nft_objects", return_value=objects):
            self.assertEqual([(str(net), span) for net, span, _ in fw.nft_forwards(set())],
                             [("192.0.2.8/32", (4000, 4050)), ("192.0.2.9/32", (4100, 4100))])
            objects[0]["map"]["name"] = "other"
            self.assertEqual([(str(net), span) for net, span, _ in fw.nft_forwards(set())],
                             [("0.0.0.0/0", (1, 65535))])

    def test_failed_rollback_preserves_previous_intent_for_next_sync(self):
        fw, fake = module(), FakeIptables()
        original = copy.deepcopy(fake.tables)
        def fail(*args, **kwargs):
            if args[0] == "ip6tables" and "-A" in args:
                raise OSError("new rule failed")
            if args[0] == "iptables" and "-D" in args:
                raise OSError("undo failed")
            return fake(*args, **kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch.object(fw, "ROOT", Path(tmp)), \
             patch.object(fw, "backend", return_value="iptables"):
            nodes(fw.ROOT)
            with patch.object(fw, "cmd", fail), self.assertRaisesRegex(ValueError, "回滚未完成"):
                fw.manage("enable", input_position="append")
            journal = json.loads((fw.ROOT / "firewall.json").read_text())
            self.assertFalse(journal["allow"])
            self.assertTrue(journal["rules"])
            with patch.object(fw, "cmd", fake):
                fw.manage("sync")
            self.assertEqual(fake.tables, original)

    def test_admin_denies_and_later_policy_updates_precede_managed_allow(self):
        for engine in ("iptables", "ufw"):
            fw = module()
            fake = FakeIptables()
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as tmp, patch.object(fw, "ROOT", Path(tmp)), \
                 patch.object(fw, "cmd", fake), patch.object(fw, "backend", return_value=engine):
                nodes(fw.ROOT)
                chains = []
                for tool, source in (("iptables", "192.0.2.11"), ("ip6tables", "2001:db8::11")):
                    chain = "INPUT" if engine == "iptables" else ("ufw-user-input" if tool == "iptables" else "ufw6-user-input")
                    rules = fake.tables.setdefault((tool, "filter", chain), [])
                    rules.append(("-s", source, "-p", "tcp", "--dport", "2443", "-j", "DROP"))
                    chains.append((rules, source))
                before = copy.deepcopy(fake.tables)
                with self.assertRaisesRegex(ValueError, "append"):
                    fw.manage("enable")
                self.assertEqual(fake.tables, before)
                self.assertFalse((fw.ROOT / "firewall.json").exists())
                fw.manage("enable", input_position="append")
                # Existing admin rules and rules added after the last sync must both win.
                for rules, _ in chains:
                    rules.append(("-i", "private0", "-p", "tcp", "--dport", "2443", "-j", "DROP"))
                fw.manage("sync")
                def verdict(rules, source, interface):
                    for rule in rules:
                        if "--dport" not in rule or rule[rule.index("--dport") + 1] != "2443":
                            continue
                        if "-s" in rule and rule[rule.index("-s") + 1] != source:
                            continue
                        if "-i" in rule and rule[rule.index("-i") + 1] != interface:
                            continue
                        return rule[rule.index("-j") + 1]
                    return "DROP"
                for rules, blocked in chains:
                    self.assertEqual(verdict(rules, blocked, "public0"), "DROP")
                    self.assertEqual(verdict(rules, "other", "private0"), "DROP")
                    self.assertEqual(verdict(rules, "other", "public0"), "ACCEPT")

    def test_overlaps_leave_disk_and_rules_untouched(self):
        fw = module()
        fake = FakeIptables()
        with tempfile.TemporaryDirectory() as tmp, patch.object(fw, "ROOT", Path(tmp)), \
             patch.object(fw, "cmd", fake), patch.object(fw, "backend", return_value="iptables"):
            config = {"inbounds": [{"tag": "hy2-sb", "listen": "192.0.2.8", "listen_port": 3000},
                                   {"tag": "tuic5-sb", "listen": "192.0.2.8", "listen_port": 3001}]}
            (fw.ROOT / "sb.json").write_text(json.dumps(config))
            fw.manage("add", "hy2-sb", "4000:4010")
            saved = (fw.ROOT / "firewall.json").read_bytes()
            fake.sockets[6] = "UNCONN 0 0 [::]:4500 [::]:* v6only:0\n"
            fake.tables["iptables", "nat", "PREROUTING"].append(
                ("-p", "udp", "-d", "192.0.2.0/24", "--dport", "4800:4900", "-j", "DNAT",
                 "--to-destination", "192.0.2.20:5000"))
            rules = copy.deepcopy(fake.tables)
            for span, expected in [("3000", "3000"), ("2999:3002", "3000"), ("3990:4000", "4000"),
                                   ("4005:4020", "4005:4010"), ("4490:4500", "4500"), ("4700:4820", "4800:4820")]:
                with self.subTest(span=span), self.assertRaisesRegex(ValueError, "端口 " + expected + "（"):
                    fw.manage("add", "tuic5-sb", span)
                self.assertEqual(saved, (fw.ROOT / "firewall.json").read_bytes())
                self.assertEqual(rules, fake.tables)
            fw.manage("add", "tuic5-sb", "4011:4020")
            fake.sockets[6] = "UNCONN 0 0 [::]:4500 [::]:* v6only:1\n"
            fw.manage("add", "tuic5-sb", "4500")
            # Same port range at a distinct public destination is independent.
            config["inbounds"][1]["listen"] = "192.0.2.9"
            (fw.ROOT / "sb.json").write_text(json.dumps(config))
            fw.manage("add", "tuic5-sb", "4000:4010")

    def test_nft_chain_tail_and_forward_intervals(self):
        fw = module()
        objects = [{"chain": {"family": "inet", "table": "policy", "name": "input", "type": "filter", "hook": "input"}},
                   {"rule": {"family": "inet", "table": "policy", "chain": "input", "expr": [{"drop": None}]}},
                   {"rule": {"family": "inet", "table": "policy", "chain": "forward_udp", "expr": [
                       {"match": {"op": "==", "left": {"payload": {"protocol": "ip6", "field": "daddr"}},
                                  "right": {"prefix": {"addr": "2001:db8::", "len": 64}}}},
                       {"match": {"op": "in", "left": {"payload": {"protocol": "udp", "field": "dport"}},
                                  "right": {"set": [443, {"range": [4500, 4520]}]}}},
                       {"redirect": {"port": 12443}}]}}]
        with patch.object(fw, "nft_objects", return_value=objects), patch.object(fw, "cmd") as run:
            for addr in ("192.0.2.1", "2001:db8::1"):
                fw.nft_rule(("nft", "", addr, "tcp", "2443", ""), True)
                self.assertEqual(run.call_args.args[:6], ("nft", "add", "rule", "inet", "policy", "input"))
            objects.append({"chain": dict(objects[0]["chain"], name="other_input")})
            run.reset_mock()
            with self.assertRaisesRegex(ValueError, "手动放行"):
                fw.nft_rule(("nft", "", "::", "tcp", "2443", ""), True)
            run.assert_not_called()
            self.assertEqual([(str(addr), pair) for addr, pair, _ in fw.nft_forwards(set())],
                             [("2001:db8::/64", (443, 443)), ("2001:db8::/64", (4500, 4520))])

    def test_firewalld_levels_zones_and_existing_priority(self):
        fw = module()
        configs = {True: {"existing SSH service"}, False: {"existing SSH service"}}
        def fake(*args, check=True):
            self.assertEqual(args[0], "firewall-cmd")
            output = ""
            code = 0
            rules = configs["--permanent" in args]
            if args[-1] == "--get-active-zones":
                output = "public\n  interfaces: eth0\ntrusted\n  interfaces: vnet0\n"
            else:
                operation, rich = args[-1].split("=", 1)
                self.assertIn("--zone=public", args)
                if operation == "--query-rich-rule":
                    code = int(rich not in rules)
                elif operation == "--add-rich-rule":
                    rules.add(rich)
                else:
                    self.assertEqual(operation, "--remove-rich-rule")
                    rules.remove(rich)
            return subprocess.CompletedProcess(args, code, output, "")
        with tempfile.TemporaryDirectory() as tmp, patch.object(fw, "ROOT", Path(tmp)), \
             patch.object(fw, "cmd", fake), patch.object(fw, "backend", return_value="firewalld"):
            nodes(fw.ROOT)
            with self.assertRaisesRegex(ValueError, "zone"):
                fw.manage("enable")
            fw.manage("enable", zone="public")
            self.assertEqual(configs[True], configs[False])
            original_managed = next(r for r in configs[True] if "2443" in r)
            fw.manage("remove")
            self.assertEqual(configs[True], {"existing SSH service"})
            # A pre-existing rule with the same priority is never adopted or removed.
            configs[True].add(original_managed)
            with self.assertRaisesRegex(ValueError, "归属"):
                fw.manage("enable", zone="public")
            self.assertIn(original_managed, configs[True])
            self.assertEqual(configs[False], {"existing SSH service"})

    def test_dual_stack_policy_and_unrelated_rules(self):
        fw = module()
        fake = FakeIptables()
        original = copy.deepcopy(fake.tables)
        with tempfile.TemporaryDirectory() as tmp, patch.object(fw, "ROOT", Path(tmp)), \
             patch.object(fw, "cmd", fake), patch.object(fw, "backend", return_value="iptables"):
            nodes(fw.ROOT)
            fw.manage("sync")
            self.assertEqual(fake.tables, original)
            fw.manage("enable", input_position="append")
            fw.manage("add", "hy2-sb", "4000:4009")
            active = copy.deepcopy(fake.tables)
            fw.manage("enable")
            fw.manage("add", "hy2-sb", "4000:4009")
            self.assertEqual(fake.tables, active)
            for family, address in [("iptables", "192.0.2.8"), ("ip6tables", "2001:db8::8")]:
                self.assertTrue(any(address in rule and "2443" in rule for rule in fake.tables[family, "filter", "INPUT"]))
            self.assertNotIn("8899", repr(fake.tables))
            nodes(fw.ROOT, 3002)
            fw.manage("sync")
            self.assertNotIn("3001", repr(fake.tables))
            self.assertIn("3002", repr(fake.tables))
            fw.manage("remove")
            self.assertEqual(fake.tables, original)
            self.assertEqual(fake.policy, {"iptables": "DROP", "ip6tables": "DROP"})

    def test_failed_second_family_rolls_back(self):
        fw = module()
        fake = FakeIptables()
        initial = copy.deepcopy(fake.tables)
        def fail(*args, **kwargs):
            if args[0] == "iptables" and "-A" in args:
                raise OSError("simulated per-family failure")
            return fake(*args, **kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch.object(fw, "ROOT", Path(tmp)), \
             patch.object(fw, "cmd", fail), patch.object(fw, "backend", return_value="iptables"):
            nodes(fw.ROOT)
            with self.assertRaises(OSError):
                fw.manage("enable", input_position="append")
            self.assertEqual(fake.tables, initial)


class ReleaseTests(ShellTestCase):
    def test_unverified_installed_programs_are_replaced_before_use(self):
        box = self.root / "box"
        box.mkdir()
        (self.root / "tools").mkdir()
        (self.root / "tools/releases.sha256").write_text("")
        for name in ("cloudflared", "caddy"):
            file = box / name
            file.write_bytes(b"#!/bin/sh\necho executed >> counter\n")
            file.chmod(0o755)
        functions = (section("release_fetch", "inssb") + section("cloudflaredargo", "cfargoym")
                     + section("sub_caddy_prepare", "sub_caddy_reload"))
        functions = functions.replace("/etc/s-box", "box").replace("/etc/caddy", "system-caddy")
        result = self.run_shell(functions + """
SB_RELEASE_DIR=.
cpu=amd64
uname(){ echo x86_64; }; ss(){ :; }; sub_service(){ return 1; }
red(){ :; }
release_fetch(){ echo fetch >> retry; return 1; }
sub_caddy_download(){ echo caddy >> retry; return 1; }
cloudflaredargo && exit 21
sub_caddy_prepare && exit 22
[[ ! -e counter && $(cat retry) == $'fetch\ncaddy' ]]
""")
        self.assertEqual(result.returncode, 0)

    def test_installer_and_switch_share_the_release_gate(self):
        box = self.root / "box"
        box.mkdir()
        (self.root / "tools").mkdir()
        (box / "sb.json").write_text("{}")
        (box / "sb11.json").write_text("{}")
        name = "sing-box-1.14.3-linux-amd64"
        payload = b"#!/bin/bash\nprintf '%s\\n' \"$1\" >> core-calls\necho 'sing-box version 1.14.3'\n"
        archive = self.root / (name + ".tar.gz")
        with tarfile.open(archive, "w:gz") as writer:
            member = tarfile.TarInfo(name + "/sing-box")
            member.size = len(payload)
            writer.addfile(member, io.BytesIO(payload))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest = self.root / "tools" / "releases.sha256"
        functions = (section("release_fetch", "inscertificate") + section("core_update", "upsbcroe")
                     ).replace("/etc/s-box", "box")
        common = functions + """
SB_RELEASE_DIR=.
red(){ :; }; blue(){ :; }; yellow(){ :; }
readp(){ printf -v "$2" 1; }
restartsb(){ :; }; sbshare(){ :; }
cpu=amd64
"""
        for action in ("inssb", "core_update 1.14.3"):
            for valid in (False, True):
                with self.subTest(action=action, valid=valid):
                    installed = box / "sing-box"
                    installed.write_bytes(b"previous")
                    counter = self.root / "core-calls"
                    if counter.exists():
                        counter.unlink()
                    value = digest if valid else "9" * 64
                    manifest.write_text(f"{value}  {archive.name}  bundle:{archive.name}\n")
                    result = self.run_shell(common + action, check=False)
                    if valid:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(installed.read_bytes(), payload)
                        expected = ["version"] if action == "inssb" else ["version", "check"]
                        self.assertEqual(counter.read_text().splitlines(), expected)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse(counter.exists())
                        self.assertEqual(installed.read_bytes(), b"previous")

    def test_corrupt_or_missing_lock_never_executes_candidate(self):
        (self.root / "box").mkdir()
        (self.root / "tools").mkdir()
        payload = b"#!/bin/sh\nprintf 'called\\n' >> counter\n"
        archive = self.root / "archive.tar.gz"
        with tarfile.open(archive, "w:gz") as writer:
            member = tarfile.TarInfo("caddy")
            member.size = len(payload)
            writer.addfile(member, io.BytesIO(payload))
        expected = hashlib.sha256(archive.read_bytes()).hexdigest()
        body = (section("release_fetch", "inssb") + section("sub_caddy_download", "sub_caddy_prepare")).replace(
            "/etc/s-box", "box")
        script = body + """
red(){ :; }
curl(){
    local target
    while (( $# )); do
        if [[ "$1" == -o ]]; then target=$2; shift; fi
        shift
    done
    cp archive.tar.gz "$target"
}
SB_RELEASE_DIR=.
sub_caddy_download amd64
"""
        asset = "caddy_2.11.9_linux_amd64.tar.gz"
        manifest = self.root / "tools" / "releases.sha256"
        installed = self.root / "box" / "caddy"
        for digest in ("", "0" * 64, expected):
            with self.subTest(digest=digest):
                installed.write_bytes(b"old program")
                manifest.write_text(f"{digest}  {asset}  https://release.invalid/archive\n" if digest else "")
                result = self.run_shell(script, check=False)
                if digest == expected:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual((self.root / "counter").read_text().splitlines(), ["called"])
                    self.assertEqual(installed.read_bytes(), payload)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((self.root / "counter").exists())
                    self.assertEqual(installed.read_bytes(), b"old program")

    def test_locked_bundled_binaries(self):
        for row in (REPO / "tools/releases.sha256").read_text().splitlines():
            if "  bundle:" in row:
                digest, name, url = row.split()
                self.assertEqual(hashlib.sha256((REPO / name).read_bytes()).hexdigest(), digest)
                self.assertEqual(url, "bundle:" + name)


def namespace_scenario(parent, engine):
    # Even an incorrectly configured runner cannot reach the host's net namespace here.
    if os.readlink("/proc/self/ns/net") == parent:
        raise RuntimeError("isolated network namespace required")
    fw = module()
    def system(tool, *args):
        return subprocess.check_output([tool, *args], text=True)
    system("ip", "link", "set", "lo", "up")
    for address in ("192.0.2.8/32", "192.0.2.9/32"):
        system("ip", "addr", "add", address, "dev", "lo")
    for address in ("2001:db8::8/128", "2001:db8::9/128"):
        system("ip", "-6", "addr", "add", address, "dev", "lo", "nodad")
    if engine == "nft":
        system("nft", "add table inet fixture; add chain inet fixture input { type filter hook input priority 0; policy drop; }; "
               "add rule inet fixture input tcp dport 2222 accept; "
               "add rule inet fixture input ip saddr 192.0.2.9 udp dport 3001 drop; "
               "add rule inet fixture input ip6 saddr 2001:db8::9 udp dport 3001 drop; "
               "add chain inet fixture prerouting { type nat hook prerouting priority -100; }; "
               "add rule inet fixture prerouting udp dport 1235 redirect to :1234")
        def snapshot():
            return system("nft", "-n", "list", "ruleset")
    else:
        caps = ("iptables", "ip6tables")
        for tool in caps:
            system(tool, "-P", "INPUT", "DROP")
            system(tool, "-A", "INPUT", "-p", "tcp", "--dport", "2222", "-j", "ACCEPT")
            system(tool, "-A", "INPUT", "-s", "192.0.2.9" if tool == "iptables" else "2001:db8::9",
                   "-p", "udp", "--dport", "3001", "-j", "DROP")
            system(tool, "-t", "nat", "-A", "PREROUTING", "-p", "udp", "--dport", "1235",
                   "-j", "REDIRECT", "--to-ports", "1234")
        def snapshot():
            return [(system(tool, "-S"), system(tool, "-t", "nat", "-S")) for tool in caps]
    start = snapshot()
    def assert_retained(current):
        if engine == "nft":
            for token in ("policy drop", "tcp dport 2222 accept", "udp dport 1235 redirect to :1234"):
                assert token in current, token
        else:
            for initial_family, current_family in zip(start, current):
                for original_table, active_table in zip(initial_family, current_family):
                    assert set(original_table.splitlines()) <= set(active_table.splitlines())
    with tempfile.TemporaryDirectory() as tmp, patch.object(fw, "ROOT", Path(tmp)), \
         patch.object(fw, "backend", return_value=engine):
        nodes(fw.ROOT)
        fw.manage("enable", input_position="append")
        fw.manage("add", "hy2-sb", "4000:4010")
        once = snapshot()
        assert_retained(once)
        for family, allowed, blocked in ((socket.AF_INET, "192.0.2.8", "192.0.2.9"),
                                         (socket.AF_INET6, "2001:db8::8", "2001:db8::9")):
            with socket.socket(family, socket.SOCK_DGRAM) as receiver:
                receiver.bind((allowed, 3001))
                receiver.settimeout(0.5)
                for source in (blocked, allowed):
                    with socket.socket(family, socket.SOCK_DGRAM) as sender:
                        sender.bind((source, 0))
                        sender.sendto(source.encode(), (allowed, 3001))
                assert receiver.recvfrom(256)[0].decode() == allowed
        fw.manage("enable")
        assert snapshot() == once
        nodes(fw.ROOT, 3002)
        fw.manage("sync")
        changed = snapshot()
        assert "3002" in str(changed)
        assert_retained(changed)
        fw.manage("remove")
        assert snapshot() == start
    print(system(engine, "--version").strip())


class NamespaceTest(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux" and all(shutil.which(x) for x in ("unshare", "ip", "ss", "iptables", "ip6tables")),
                         "requires Linux network namespaces, iproute2 and both iptables families")
    def test_kernel_rules(self):
        if subprocess.run(["unshare", "-n", "true"], capture_output=True).returncode:
            self.skipTest("network namespace creation is unavailable")
        for engine in ["iptables"] + (["nft"] if shutil.which("nft") else []):
            with self.subTest(engine=engine):
                result = subprocess.run(["unshare", "-n", sys.executable, __file__, "--isolated",
                                         os.readlink("/proc/self/ns/net"), engine], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                print(result.stdout.strip())


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--isolated":
        namespace_scenario(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
