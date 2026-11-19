"""Exercise the release gate and the firewall policy without touching the host firewall."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
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
        for binary in ("iptables", "ip6tables"):
            self.policy[binary] = "DROP"
            self.tables[binary, "filter", "INPUT"] = [("-p", "tcp", "--dport", "2222", "-j", "ACCEPT")]
            self.tables[binary, "nat", "PREROUTING"] = [
                ("-p", "udp", "--dport", "1235", "-j", "DNAT", "--to-destination", ":1234")]

    def __call__(self, binary, wait, seconds, opt, table, action, chain, *args, check=True):
        assert (wait, opt) == ("-w", "-t")
        rules = self.tables[binary, table, chain]
        result = 0
        if action == "-C":
            result = 0 if args in rules else 1
        elif action == "-I":
            rules.insert(0, args)
        elif action == "-D":
            rules.remove(args)
        elif action != "-S":
            raise AssertionError(action)
        return subprocess.CompletedProcess([], result, "", "")


class FirewallTests(unittest.TestCase):
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
            fw.manage("enable")
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
            if args[0] == "iptables" and "-I" in args:
                raise OSError("simulated per-family failure")
            return fake(*args, **kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch.object(fw, "ROOT", Path(tmp)), \
             patch.object(fw, "cmd", fail), patch.object(fw, "backend", return_value="iptables"):
            nodes(fw.ROOT)
            with self.assertRaises(OSError):
                fw.manage("enable")
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
    if engine == "nft":
        system("nft", "add table inet fixture; add chain inet fixture input { type filter hook input priority 0; policy drop; }; "
               "add rule inet fixture input tcp dport 2222 accept; "
               "add chain inet fixture prerouting { type nat hook prerouting priority -100; }; "
               "add rule inet fixture prerouting udp dport 1235 redirect to :1234")
        def snapshot():
            return system("nft", "-n", "list", "ruleset")
    else:
        caps = ("iptables", "ip6tables")
        for tool in caps:
            system(tool, "-P", "INPUT", "DROP")
            system(tool, "-A", "INPUT", "-p", "tcp", "--dport", "2222", "-j", "ACCEPT")
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
        fw.manage("enable")
        fw.manage("add", "hy2-sb", "4000:4010")
        once = snapshot()
        assert_retained(once)
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
    @unittest.skipUnless(sys.platform == "linux" and all(shutil.which(x) for x in ("unshare", "iptables", "ip6tables")),
                         "requires Linux network namespaces and both iptables families")
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
